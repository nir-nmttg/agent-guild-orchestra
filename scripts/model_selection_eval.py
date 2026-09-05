#!/usr/bin/env python3
"""Validate and summarize real Agent Guild Orchestra benchmark records.

This tool never simulates model behavior. Offline fixtures exercise only the
recording and accounting code and are always labelled as such.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "scripts/model_selection_eval.yaml"
STRATEGIES = {"astra_only", "astra_luna", "v2_4_baseline"}
SPLITS = {"pilot", "holdout"}
ROLES = {"root", "worker", "review"}
STAGE_STATUSES = {"completed", "failed"}


class EvalError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvalError(f"{path} must contain an object")
    return value


def validate_manifest(value: dict[str, Any]) -> None:
    if value.get("schema") != "agent-guild-model-benchmark-v3":
        raise EvalError("unsupported benchmark manifest schema")
    strategies = value.get("strategies")
    if not isinstance(strategies, dict) or set(strategies) != STRATEGIES:
        raise EvalError(f"strategies must be exactly {sorted(STRATEGIES)}")
    astra_only = strategies["astra_only"]
    astra_luna = strategies["astra_luna"]
    baseline = strategies["v2_4_baseline"]
    for label, strategy in strategies.items():
        if not isinstance(strategy, dict):
            raise EvalError(f"strategy {label} must be an object")
    for label in ("astra_only", "astra_luna"):
        if strategies[label].get("risk_review") != {"model": "gpt-6-astra", "reasoning_effort": "high"}:
            raise EvalError(f"strategy {label} must use the same Astra/high risk review")
    if astra_only.get("root") != {"model": "gpt-6-astra", "reasoning_effort": "high"}:
        raise EvalError("astra_only root must be Astra/high")
    if astra_only.get("implementation") != "root":
        raise EvalError("astra_only must keep implementation in the root session")
    if astra_luna.get("root") != {"model": "gpt-6-astra", "reasoning_effort": "high"}:
        raise EvalError("astra_luna root must be Astra/high")
    if astra_luna.get("implementation") != {"model": "gpt-5.6-luna", "reasoning_effort": "max"}:
        raise EvalError("astra_luna implementation must be Luna/max")
    if baseline.get("frozen_release") != "2.4.0":
        raise EvalError("baseline must identify the frozen v2.4 release")
    if baseline.get("implementation") != "frozen v2.4 routing" or baseline.get("risk_review") != "frozen v2.4 review routing":
        raise EvalError("baseline must execute the frozen v2.4 implementation and review policy")
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise EvalError("tasks must be a non-empty list")
    seen: set[str] = set()
    splits: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise EvalError("each task needs a string id")
        task_id = task["id"]
        if task_id in seen:
            raise EvalError(f"duplicate task id: {task_id}")
        seen.add(task_id)
        split = task.get("split")
        if split not in SPLITS:
            raise EvalError(f"task {task_id} has invalid split")
        splits.add(split)
        if not isinstance(task.get("objective"), str) or not task["objective"].strip():
            raise EvalError(f"task {task_id} needs an objective")
        if not isinstance(task.get("acceptance"), list) or not task["acceptance"]:
            raise EvalError(f"task {task_id} needs acceptance criteria")
        if any(not isinstance(item, str) or not item.strip() for item in task["acceptance"]):
            raise EvalError(f"task {task_id} acceptance criteria must be non-empty strings")
    if splits != SPLITS:
        raise EvalError("manifest needs both pilot and holdout tasks")


def records(path: Path) -> Iterable[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvalError(f"cannot read results: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalError(f"results line {line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise EvalError(f"results line {line_number}: expected object")
        yield value


def optional_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"{label} must be a number or null")
    result = float(value)
    if result < 0 or not math.isfinite(result):
        raise EvalError(f"{label} must be finite and non-negative")
    return result


def nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise EvalError(f"{label} must be a non-empty string")
    return value


def expected_model(manifest: dict[str, Any], strategy: str, role: str) -> dict[str, str] | None:
    strategy_definition = manifest["strategies"][strategy]
    if role == "root" and isinstance(strategy_definition.get("root"), dict):
        return strategy_definition["root"]
    if role == "worker" and isinstance(strategy_definition.get("implementation"), dict):
        return strategy_definition["implementation"]
    if role == "review" and isinstance(strategy_definition.get("risk_review"), dict):
        return strategy_definition["risk_review"]
    return None


def validate_record(record: dict[str, Any], manifest: dict[str, Any]) -> None:
    required = {
        "task_id", "strategy", "split", "accepted", "attempts", "evidence_kind",
        "provenance", "task_input", "acceptance_evidence",
    }
    if set(record) != required:
        raise EvalError(f"record fields must be exactly {sorted(required)}")
    tasks = {task["id"]: task for task in manifest["tasks"]}
    task_id = record["task_id"]
    if task_id not in tasks:
        raise EvalError(f"unknown task_id: {task_id}")
    if record["strategy"] not in STRATEGIES:
        raise EvalError(f"unknown strategy: {record['strategy']}")
    if record["split"] != tasks[task_id]["split"]:
        raise EvalError(f"split mismatch for {task_id}")
    if not isinstance(record["accepted"], bool):
        raise EvalError(f"accepted must be boolean for {task_id}")
    if record["evidence_kind"] not in {"live_model_run", "offline_fixture"}:
        raise EvalError(f"invalid evidence_kind for {task_id}")
    provenance = record["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"run_id", "target_revision", "codex_version"}:
        raise EvalError(f"{task_id} provenance fields are invalid")
    nonempty_string(provenance["run_id"], f"{task_id} provenance.run_id")
    if record["evidence_kind"] == "live_model_run":
        revision = nonempty_string(provenance["target_revision"], f"{task_id} provenance.target_revision")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            raise EvalError(f"{task_id} live target_revision must be a full Git object id")
        nonempty_string(provenance["codex_version"], f"{task_id} provenance.codex_version")
    elif provenance["target_revision"] is not None or provenance["codex_version"] is not None:
        raise EvalError(f"{task_id} offline fixture must not claim live revision/version provenance")
    if record["task_input"] != tasks[task_id]["objective"]:
        raise EvalError(f"task_input mismatch for {task_id}")
    evidence = record["acceptance_evidence"]
    criteria = tasks[task_id]["acceptance"]
    if not isinstance(evidence, list) or len(evidence) != len(criteria):
        raise EvalError(f"{task_id} acceptance_evidence must cover every manifest criterion")
    passed: list[bool] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"criterion", "passed", "evidence"}:
            raise EvalError(f"{task_id} acceptance_evidence {index} has invalid fields")
        if item["criterion"] != criteria[index] or not isinstance(item["passed"], bool):
            raise EvalError(f"{task_id} acceptance_evidence {index} does not match the rubric")
        nonempty_string(item["evidence"], f"{task_id} acceptance_evidence {index}.evidence")
        passed.append(item["passed"])
    if record["accepted"] != all(passed):
        raise EvalError(f"{task_id} accepted must equal the complete acceptance rubric result")

    attempts = record["attempts"]
    if not isinstance(attempts, list) or not attempts:
        raise EvalError(f"attempts must be a non-empty list for {task_id}")
    for attempt_index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, dict) or set(attempt) != {"attempt", "accepted", "stages"}:
            raise EvalError(f"{task_id} attempt {attempt_index} has invalid fields")
        if attempt["attempt"] != attempt_index or not isinstance(attempt["accepted"], bool):
            raise EvalError(f"{task_id} attempts must be sequential and carry boolean accepted")
        if attempt_index < len(attempts) and attempt["accepted"]:
            raise EvalError(f"{task_id} only the final attempt may be accepted")
        stages = attempt["stages"]
        if not isinstance(stages, list) or not stages:
            raise EvalError(f"{task_id} attempt {attempt_index} stages must be non-empty")
        observed: set[str] = set()
        failed = False
        for stage_index, stage in enumerate(stages):
            required_stage = {"role", "model", "reasoning_effort", "status", "tokens", "cost_usd"}
            if not isinstance(stage, dict) or set(stage) != required_stage:
                raise EvalError(f"{task_id} attempt {attempt_index} stage {stage_index} has invalid fields")
            role = stage["role"]
            if role not in ROLES or stage["status"] not in STAGE_STATUSES:
                raise EvalError(f"{task_id} attempt {attempt_index} stage {stage_index} role/status is invalid")
            observed.add(role)
            failed = failed or stage["status"] == "failed"
            model = nonempty_string(stage["model"], f"{task_id} stage model")
            effort = nonempty_string(stage["reasoning_effort"], f"{task_id} stage reasoning_effort")
            expected = expected_model(manifest, record["strategy"], role)
            if expected is not None and (model != expected["model"] or effort != expected["reasoning_effort"]):
                raise EvalError(f"{task_id} {record['strategy']} {role} model/effort mismatch")
            optional_number(stage["tokens"], f"{task_id} attempt {attempt_index} stage {stage_index} tokens")
            optional_number(stage["cost_usd"], f"{task_id} attempt {attempt_index} stage {stage_index} cost_usd")
        if "root" not in observed:
            raise EvalError(f"{task_id} attempt {attempt_index} must account for root usage")
        if record["strategy"] == "astra_only" and "worker" in observed:
            raise EvalError(f"{task_id} astra_only may not record a worker stage")
        if record["strategy"] == "astra_luna" and "worker" not in observed:
            raise EvalError(f"{task_id} astra_luna attempt must account for Luna worker usage")
        if not attempt["accepted"] and not failed:
            raise EvalError(f"{task_id} unsuccessful attempt {attempt_index} must identify a failed stage")
        if attempt["accepted"] and failed:
            raise EvalError(f"{task_id} accepted attempt {attempt_index} may not contain a failed stage")
    if attempts[-1]["accepted"] != record["accepted"]:
        raise EvalError(f"{task_id} final attempt outcome must match record accepted")
    if "review" not in {stage["role"] for stage in attempts[-1]["stages"]}:
        raise EvalError(f"{task_id} final attempt must account for independent review")


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["evidence_kind"], row["split"], row["strategy"])].append(row)
    output: dict[str, Any] = {"groups": []}
    for (kind, split, strategy), values in sorted(grouped.items()):
        token_values: list[float] = []
        cost_values: list[float] = []
        tokens_complete = True
        costs_complete = True
        for row in values:
            for attempt in row["attempts"]:
                for stage in attempt["stages"]:
                    tokens = optional_number(stage["tokens"], "tokens")
                    cost = optional_number(stage["cost_usd"], "cost_usd")
                    if tokens is None:
                        tokens_complete = False
                    else:
                        token_values.append(tokens)
                    if cost is None:
                        costs_complete = False
                    else:
                        cost_values.append(cost)
        assigned = len(values)
        accepted = sum(1 for row in values if row["accepted"])
        output["groups"].append(
            {
                "evidence_kind": kind,
                "split": split,
                "strategy": strategy,
                "assigned_tasks": assigned,
                "accepted_tasks": accepted,
                "acceptance_rate": accepted / assigned,
                "attempts": sum(len(row["attempts"]) for row in values),
                "total_tokens": sum(token_values) if tokens_complete else None,
                "total_cost_usd": sum(cost_values) if costs_complete else None,
            }
        )
    output["claims"] = (
        "Offline fixtures validate accounting only; they provide no model-quality or savings evidence."
        if rows and all(row["evidence_kind"] == "offline_fixture" for row in rows)
        else "Live results are descriptive. A savings claim requires complete usage and comparable accepted-task coverage."
    )
    return output


def validate_coverage(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    represented = {(row["evidence_kind"], row["split"]) for row in rows}
    actual = {(row["evidence_kind"], row["split"], row["task_id"], row["strategy"]) for row in rows}
    for kind, split in represented:
        task_ids = {task["id"] for task in manifest["tasks"] if task["split"] == split}
        expected = {(kind, split, task_id, strategy) for task_id in task_ids for strategy in STRATEGIES}
        missing = expected - actual
        extra = {item for item in actual if item[0] == kind and item[1] == split} - expected
        if missing or extra:
            raise EvalError(
                f"{kind}/{split} result matrix is incomplete: "
                f"missing={sorted((item[2], item[3]) for item in missing)}, "
                f"extra={sorted((item[2], item[3]) for item in extra)}"
            )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="v3 model benchmark plan and result accounting")
    value.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    group = value.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--validate-results", type=Path)
    group.add_argument("--summarize", type=Path)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        validate_manifest(manifest)
        if args.plan:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0
        result_path = args.validate_results or args.summarize
        rows = list(records(result_path))
        if not rows:
            raise EvalError("results are empty")
        keys: set[tuple[str, str]] = set()
        run_ids: set[str] = set()
        for row in rows:
            validate_record(row, manifest)
            key = (row["task_id"], row["strategy"])
            if key in keys:
                raise EvalError(f"duplicate task/strategy result: {key}")
            keys.add(key)
            run_id = row["provenance"]["run_id"]
            if run_id in run_ids:
                raise EvalError(f"duplicate run_id: {run_id}")
            run_ids.add(run_id)
        validate_coverage(rows, manifest)
        if args.summarize:
            print(json.dumps(aggregate(rows), ensure_ascii=False, indent=2))
        else:
            print(f"validated {len(rows)} result records")
        return 0
    except EvalError as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
