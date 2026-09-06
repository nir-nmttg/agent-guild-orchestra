#!/usr/bin/env python3
"""Validate and summarize adaptive Agent Guild Orchestra evaluation records.

This tool validates the shape and accounting of records supplied by a real
pilot or holdout run. Synthetic fixtures and manually entered records remain
separate from observed runs; the tool never simulates a model, collects Codex
usage, or turns token counts into a price claim.
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
STRATEGIES = {"astra_only", "astra_luna"}
SPLITS = {"pilot", "holdout"}
ROLES = {"root", "worker", "review"}
STAGE_STATUSES = {"completed", "failed"}
EVIDENCE_KINDS = {"synthetic_fixture", "manual_record", "observed_model_run"}
MEASUREMENT_SOURCES = {"observed", "manual", "synthetic", "unknown"}
COST_SOURCES = {"account_reported", "api_estimate", "manual", "synthetic", "unknown"}
RISKS = {"low", "medium", "high"}
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
GIT_OBJECT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SOURCE_POLICIES = {
    "synthetic_fixture": {
        "usage": {"synthetic", "unknown"},
        "wall": {"synthetic", "unknown"},
        "cost": {"synthetic", "unknown"},
    },
    "manual_record": {
        "usage": {"manual", "unknown"},
        "wall": {"manual", "unknown"},
        "cost": {"manual", "unknown"},
    },
    "observed_model_run": {
        "usage": {"observed", "unknown"},
        "wall": {"observed", "unknown"},
        "cost": {"account_reported", "api_estimate", "unknown"},
    },
}


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


def nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise EvalError(f"{label} must be a non-empty string")
    return value


def optional_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"{label} must be a number or null")
    result = float(value)
    if result < 0 or not math.isfinite(result):
        raise EvalError(f"{label} must be finite and non-negative")
    return result


def optional_integer(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvalError(f"{label} must be a non-negative integer or null")
    return value


def validate_model_pair(value: object, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"model", "reasoning_effort"}:
        raise EvalError(f"{label} must contain model and reasoning_effort")
    nonempty_string(value["model"], f"{label}.model")
    nonempty_string(value["reasoning_effort"], f"{label}.reasoning_effort")


def validate_manifest(value: dict[str, Any]) -> None:
    if value.get("schema") != "agent-guild-model-benchmark-v3":
        raise EvalError("unsupported benchmark manifest schema")
    strategies = value.get("strategies")
    if not isinstance(strategies, dict) or set(strategies) != STRATEGIES:
        raise EvalError(f"strategies must be exactly {sorted(STRATEGIES)}")
    for label, strategy in strategies.items():
        if not isinstance(strategy, dict):
            raise EvalError(f"strategy {label} must be an object")
        validate_model_pair(strategy.get("root"), f"strategy {label}.root")
        review = strategy.get("risk_review")
        if not isinstance(review, dict) or set(review) != {"model", "reasoning_effort", "mode"}:
            raise EvalError(f"strategy {label} risk_review must declare a model and mode")
        validate_model_pair(
            {key: review[key] for key in ("model", "reasoning_effort")},
            f"strategy {label}.risk_review",
        )
        if review.get("mode") != "risk_based":
            raise EvalError(f"strategy {label} risk_review must be risk_based")

    astra_only = strategies["astra_only"]
    if astra_only["root"] != {"model": "gpt-6-astra", "reasoning_effort": "high"}:
        raise EvalError("astra_only root must be Astra/high")
    if astra_only.get("implementation") != {"mode": "root"}:
        raise EvalError("astra_only implementation must remain in the root session")
    astra_luna = strategies["astra_luna"]
    if astra_luna["root"] != {"model": "gpt-6-astra", "reasoning_effort": "high"}:
        raise EvalError("astra_luna root must be Astra/high")
    if astra_luna.get("implementation") != {
        "mode": "adaptive",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
    }:
        raise EvalError("astra_luna implementation must be adaptive Luna/max")
    for label in STRATEGIES:
        review = strategies[label]["risk_review"]
        if review.get("model") != "gpt-6-astra" or review.get("reasoning_effort") != "xhigh":
            raise EvalError(f"strategy {label} must use an independent Astra/xhigh risk review")

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
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            raise EvalError(f"task {task_id} needs acceptance criteria")
        if any(not isinstance(item, str) or not item.strip() for item in acceptance):
            raise EvalError(f"task {task_id} acceptance criteria must be non-empty strings")
        features = task.get("features")
        if not isinstance(features, list) or not features or any(not isinstance(item, str) or not item.strip() for item in features):
            raise EvalError(f"task {task_id} needs non-empty feature labels")
        if task.get("risk") not in RISKS:
            raise EvalError(f"task {task_id} has invalid risk")
        if not isinstance(task.get("review_required"), bool):
            raise EvalError(f"task {task_id} needs a boolean review_required")
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


def expected_model(manifest: dict[str, Any], strategy: str, role: str) -> dict[str, str] | None:
    definition = manifest["strategies"][strategy]
    if role == "root" and isinstance(definition.get("root"), dict):
        return definition["root"]
    if role == "worker" and isinstance(definition.get("implementation"), dict) and definition["implementation"].get("mode") == "adaptive":
        return {
            "model": definition["implementation"]["model"],
            "reasoning_effort": definition["implementation"]["reasoning_effort"],
        }
    if role == "review" and isinstance(definition.get("risk_review"), dict):
        return {
            "model": definition["risk_review"]["model"],
            "reasoning_effort": definition["risk_review"]["reasoning_effort"],
        }
    return None


def record_root_pair(record: dict[str, Any], task_id: str) -> tuple[str, str]:
    pairs = {
        (event["model"], event["reasoning_effort"])
        for attempt in record["attempts"]
        for event in attempt["stages"]
        if event["role"] == "root"
    }
    if len(pairs) != 1:
        raise EvalError(f"{task_id} root model/effort must remain consistent across attempts")
    return next(iter(pairs))


def validate_provenance(provenance: object, kind: str, task_id: str) -> None:
    required = {
        "run_id", "session_ref", "target_revision", "codex_version",
        "config_digest", "prompt_bundle_digest", "skill_bundle_digest",
        "fresh_context", "permission_evidence", "root_override",
    }
    if not isinstance(provenance, dict) or set(provenance) != required:
        raise EvalError(f"{task_id} provenance fields are invalid")
    nonempty_string(provenance["run_id"], f"{task_id} provenance.run_id")
    if not isinstance(provenance["root_override"], bool):
        raise EvalError(f"{task_id} provenance.root_override must be boolean")
    if not isinstance(provenance["fresh_context"], bool):
        raise EvalError(f"{task_id} provenance.fresh_context must be boolean")
    nullable_strings = (
        "session_ref", "target_revision", "codex_version", "config_digest",
        "prompt_bundle_digest", "skill_bundle_digest", "permission_evidence",
    )
    for field in nullable_strings:
        if provenance[field] is not None:
            nonempty_string(provenance[field], f"{task_id} provenance.{field}")

    if kind == "observed_model_run":
        revision = nonempty_string(provenance["target_revision"], f"{task_id} provenance.target_revision")
        if GIT_OBJECT_RE.fullmatch(revision) is None:
            raise EvalError(f"{task_id} observed target_revision must be a full Git object id")
        for field in ("session_ref", "codex_version", "permission_evidence"):
            nonempty_string(provenance[field], f"{task_id} provenance.{field}")
        for field in ("config_digest", "prompt_bundle_digest", "skill_bundle_digest"):
            digest = nonempty_string(provenance[field], f"{task_id} provenance.{field}")
            if SHA256_RE.fullmatch(digest) is None:
                raise EvalError(f"{task_id} observed {field} must be a sha256 digest")
        if provenance["fresh_context"] is not True:
            raise EvalError(f"{task_id} observed model run must record a fresh context")
    elif kind == "synthetic_fixture":
        for field in nullable_strings:
            if provenance[field] is not None:
                raise EvalError(f"{task_id} synthetic fixture must not claim {field} provenance")
        if provenance["fresh_context"] is not False or provenance["root_override"] is not False:
            raise EvalError(f"{task_id} synthetic fixture has live-run provenance")


def validate_usage(value: object, label: str, evidence_kind: str) -> None:
    required = {"tokens", "codex_usage", "api_cost_usd", "usage_source", "api_cost_source"}
    if not isinstance(value, dict) or set(value) != required:
        raise EvalError(f"{label} usage fields are invalid")
    optional_integer(value["tokens"], f"{label}.tokens")
    optional_number(value["codex_usage"], f"{label}.codex_usage")
    optional_number(value["api_cost_usd"], f"{label}.api_cost_usd")
    if value["usage_source"] not in MEASUREMENT_SOURCES:
        raise EvalError(f"{label}.usage_source is invalid")
    if value["api_cost_source"] not in COST_SOURCES:
        raise EvalError(f"{label}.api_cost_source is invalid")
    policy = SOURCE_POLICIES[evidence_kind]
    if value["usage_source"] not in policy["usage"]:
        raise EvalError(f"{label}.usage_source is inconsistent with {evidence_kind}")
    if value["api_cost_source"] not in policy["cost"]:
        raise EvalError(f"{label}.api_cost_source is inconsistent with {evidence_kind}")
    if value["api_cost_usd"] is None and value["api_cost_source"] not in {"unknown", "synthetic"}:
        raise EvalError(f"{label}.api_cost_source must be unknown when api_cost_usd is null")
    if value["api_cost_usd"] is not None and value["api_cost_source"] in {"unknown", "synthetic"}:
        raise EvalError(f"{label}.api_cost_source must identify a non-null cost")


def validate_event(
    event: object,
    *,
    task_id: str,
    strategy: str,
    evidence_kind: str,
    provenance: dict[str, Any],
    attempt_index: int,
    event_index: int,
    invocation_ids: set[str],
    manifest: dict[str, Any],
) -> str:
    required = {
        "sequence", "invocation_id", "role", "model", "reasoning_effort",
        "status", "failure_evidence", "usage", "elapsed_seconds", "evidence_refs",
    }
    if not isinstance(event, dict) or set(event) != required:
        raise EvalError(f"{task_id} attempt {attempt_index} event {event_index} has invalid fields")
    if event["sequence"] != event_index + 1:
        raise EvalError(f"{task_id} attempt {attempt_index} events must preserve sequence order")
    invocation_id = nonempty_string(event["invocation_id"], f"{task_id} invocation_id")
    if invocation_id in invocation_ids:
        raise EvalError(f"duplicate invocation_id: {invocation_id}")
    invocation_ids.add(invocation_id)
    role = event["role"]
    if role not in ROLES or event["status"] not in STAGE_STATUSES:
        raise EvalError(f"{task_id} attempt {attempt_index} event {event_index} role/status is invalid")
    model = nonempty_string(event["model"], f"{task_id} event model")
    effort = nonempty_string(event["reasoning_effort"], f"{task_id} event reasoning_effort")
    expected = expected_model(manifest, strategy, role)
    root_override = role == "root" and provenance["root_override"] is True
    if strategy == "astra_only" and role == "worker":
        raise EvalError(f"{task_id} astra_only may not record a worker event")
    if expected is not None:
        if root_override and role == "root":
            if model != expected["model"]:
                raise EvalError(f"{task_id} root model must remain {expected['model']}; root_override only permits effort changes")
        elif model != expected["model"] or effort != expected["reasoning_effort"]:
            raise EvalError(f"{task_id} {strategy} {role} model/effort mismatch")
    failure = event["failure_evidence"]
    if event["status"] == "failed":
        nonempty_string(failure, f"{task_id} failed event {event_index}.failure_evidence")
    elif failure is not None:
        raise EvalError(f"{task_id} completed event {event_index} may not carry failure_evidence")
    validate_usage(event["usage"], f"{task_id} attempt {attempt_index} event {event_index}", evidence_kind)
    optional_number(event["elapsed_seconds"], f"{task_id} attempt {attempt_index} event {event_index} elapsed_seconds")
    refs = event["evidence_refs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise EvalError(f"{task_id} attempt {attempt_index} event {event_index} needs evidence_refs")
    return role


def validate_record(record: dict[str, Any], manifest: dict[str, Any], invocation_ids: set[str] | None = None) -> None:
    required = {
        "task_id", "strategy", "split", "accepted", "attempts", "evidence_kind",
        "provenance", "task_input", "acceptance_evidence", "grade_refs",
    }
    if set(record) != required:
        raise EvalError(f"record fields must be exactly {sorted(required)}")
    tasks = {task["id"]: task for task in manifest["tasks"]}
    task_id = record["task_id"]
    if task_id not in tasks:
        raise EvalError(f"unknown task_id: {task_id}")
    strategy = record["strategy"]
    if strategy not in STRATEGIES:
        raise EvalError(f"unknown strategy: {strategy}")
    task = tasks[task_id]
    if record["split"] != task["split"]:
        raise EvalError(f"split mismatch for {task_id}")
    if not isinstance(record["accepted"], bool):
        raise EvalError(f"accepted must be boolean for {task_id}")
    kind = record["evidence_kind"]
    if kind not in EVIDENCE_KINDS:
        raise EvalError(f"invalid evidence_kind for {task_id}")
    validate_provenance(record["provenance"], kind, task_id)
    provenance = record["provenance"]
    assert isinstance(provenance, dict)
    if record["task_input"] != task["objective"]:
        raise EvalError(f"task_input mismatch for {task_id}")
    grade_refs = record["grade_refs"]
    if not isinstance(grade_refs, list) or not grade_refs or any(not isinstance(ref, str) or not ref.strip() for ref in grade_refs):
        raise EvalError(f"{task_id} grade_refs must contain reproducible test/diff/grade references")

    evidence = record["acceptance_evidence"]
    criteria = task["acceptance"]
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
    all_invocation_ids = invocation_ids if invocation_ids is not None else set()
    for attempt_index, attempt in enumerate(attempts, 1):
        attempt_required = {"attempt", "accepted", "wall_time_seconds", "wall_time_source", "stages"}
        if not isinstance(attempt, dict) or set(attempt) != attempt_required:
            raise EvalError(f"{task_id} attempt {attempt_index} has invalid fields")
        if attempt["attempt"] != attempt_index or not isinstance(attempt["accepted"], bool):
            raise EvalError(f"{task_id} attempts must be sequential and carry boolean accepted")
        if attempt_index < len(attempts) and attempt["accepted"]:
            raise EvalError(f"{task_id} only the final attempt may be accepted")
        optional_number(attempt["wall_time_seconds"], f"{task_id} attempt {attempt_index} wall_time_seconds")
        if attempt["wall_time_source"] not in MEASUREMENT_SOURCES:
            raise EvalError(f"{task_id} attempt {attempt_index} wall_time_source is invalid")
        if attempt["wall_time_source"] not in SOURCE_POLICIES[kind]["wall"]:
            raise EvalError(f"{task_id} attempt {attempt_index} wall_time_source is inconsistent with {kind}")
        events = attempt["stages"]
        if not isinstance(events, list) or not events:
            raise EvalError(f"{task_id} attempt {attempt_index} events must be non-empty")
        observed_roles: set[str] = set()
        failed = False
        for event_index, event in enumerate(events):
            role = validate_event(
                event,
                task_id=task_id,
                strategy=strategy,
                evidence_kind=kind,
                provenance=provenance,
                attempt_index=attempt_index,
                event_index=event_index,
                invocation_ids=all_invocation_ids,
                manifest=manifest,
            )
            observed_roles.add(role)
            failed = failed or event["status"] == "failed"
        if "root" not in observed_roles:
            raise EvalError(f"{task_id} attempt {attempt_index} must account for root usage")
        if attempt["accepted"] and failed:
            raise EvalError(f"{task_id} accepted attempt {attempt_index} may not contain a failed event")
    record_root_pair(record, task_id)
    final_roles = {event["role"] for event in attempts[-1]["stages"]}
    if task["review_required"] and "review" not in final_roles:
        raise EvalError(f"{task_id} final attempt requires the independent review")
    if attempts[-1]["accepted"] != record["accepted"]:
        raise EvalError(f"{task_id} final attempt outcome must match record accepted")


def _sum_measurements(
    rows: list[dict[str, Any]],
    field: str,
    source_field: str,
    allowed_sources: set[str],
) -> tuple[float | None, str]:
    values: list[float] = []
    sources: set[str] = set()
    complete = True
    for row in rows:
        for attempt in row["attempts"]:
            for event in attempt["stages"]:
                usage = event["usage"]
                value = usage[field]
                source = usage[source_field]
                sources.add(source)
                if value is None or source not in allowed_sources:
                    complete = False
                else:
                    values.append(float(value))
    if not complete or not sources:
        return None, "unknown"
    return sum(values), next(iter(sources)) if len(sources) == 1 else "mixed"


def _sum_wall_time(rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    values: list[float] = []
    sources: set[str] = set()
    for row in rows:
        for attempt in row["attempts"]:
            value = attempt["wall_time_seconds"]
            source = attempt["wall_time_source"]
            sources.add(source)
            if value is None or source not in {"observed", "synthetic"}:
                return None, "unknown"
            values.append(float(value))
    return sum(values), next(iter(sources)) if len(sources) == 1 else "mixed"


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        root_model, root_effort = record_root_pair(row, row["task_id"])
        grouped[(row["evidence_kind"], row["split"], row["strategy"], root_model, root_effort)].append(row)
    output: dict[str, Any] = {"groups": []}
    for (kind, split, strategy, root_model, root_effort), values in sorted(grouped.items()):
        total_tokens, token_basis = _sum_measurements(values, "tokens", "usage_source", {"observed", "synthetic"})
        total_codex_usage, codex_basis = _sum_measurements(values, "codex_usage", "usage_source", {"observed"})
        total_account_cost, account_cost_basis = _sum_measurements(values, "api_cost_usd", "api_cost_source", {"account_reported"})
        total_api_estimate, api_estimate_basis = _sum_measurements(values, "api_cost_usd", "api_cost_source", {"api_estimate"})
        total_wall, wall_basis = _sum_wall_time(values)
        assigned = len(values)
        accepted = sum(1 for row in values if row["accepted"])
        event_count = sum(len(attempt["stages"]) for row in values for attempt in row["attempts"])
        workers = sum(
            1 for row in values for attempt in row["attempts"] for event in attempt["stages"] if event["role"] == "worker"
        )
        reviews = sum(
            1 for row in values for attempt in row["attempts"] for event in attempt["stages"] if event["role"] == "review"
        )
        output["groups"].append(
            {
                "evidence_kind": kind,
                "split": split,
                "strategy": strategy,
                "root_model": root_model,
                "root_reasoning_effort": root_effort,
                "assigned_tasks": assigned,
                "accepted_tasks": accepted,
                "acceptance_rate": accepted / assigned,
                "attempts": sum(len(row["attempts"]) for row in values),
                "event_count": event_count,
                "worker_events": workers,
                "review_events": reviews,
                "total_tokens": total_tokens,
                "token_basis": token_basis,
                "total_codex_usage": total_codex_usage,
                "codex_usage_basis": codex_basis,
                "total_cost_usd": total_account_cost,
                "cost_basis": account_cost_basis,
                "api_estimate_cost_usd": total_api_estimate,
                "api_estimate_basis": api_estimate_basis,
                "total_wall_time_seconds": total_wall,
                "wall_time_basis": wall_basis,
            }
        )
    kinds = {row["evidence_kind"] for row in rows}
    if kinds == {"synthetic_fixture"}:
        output["claims"] = "Synthetic fixtures validate schema and accounting only; they provide no model-quality, host-usage, or savings evidence."
    elif kinds == {"manual_record"}:
        output["claims"] = "Manual records are descriptive bookkeeping; they do not establish observed model quality, host usage, or savings."
    else:
        output["claims"] = (
            "Observed records remain descriptive. Missing usage stays unknown, Codex usage is separate from API USD estimates, "
            "and a quality or savings conclusion requires comparable pilot/holdout coverage and an external grade."
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
    value = argparse.ArgumentParser(description="状況に応じた委譲の有無を比べる、モデル評価計画と結果集計")
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
        invocation_ids: set[str] = set()
        for row in rows:
            validate_record(row, manifest, invocation_ids)
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
