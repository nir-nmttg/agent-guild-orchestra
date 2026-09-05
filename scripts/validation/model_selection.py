"""Focused checks for the adaptive evaluation accounting harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .core import ROOT, require


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/model_selection_eval.py"), *args],
        text=True,
        capture_output=True,
    )


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def validate_model_selection_eval() -> None:
    plan = run("--plan")
    require(plan.returncode == 0, plan.stderr)
    manifest = json.loads(plan.stdout)
    require(set(manifest["strategies"]) == {"astra_only", "astra_luna"}, "benchmark strategy matrix mismatch")
    require(all("risk" in task and "review_required" in task for task in manifest["tasks"]), "task routing policy is not explicit")

    fixture = ROOT / "scripts/validation/fixtures/model_eval_offline.jsonl"
    valid = run("--validate-results", str(fixture))
    require(valid.returncode == 0, valid.stderr)
    summary = run("--summarize", str(fixture))
    require(summary.returncode == 0, summary.stderr)
    value = json.loads(summary.stdout)
    require(all(group["evidence_kind"] == "synthetic_fixture" for group in value["groups"]), "synthetic evidence label was lost")
    require(all(group["total_cost_usd"] is None for group in value["groups"]), "unknown cost was converted to zero")
    require(all(group["token_basis"] in {"synthetic", "unknown"} for group in value["groups"]), "synthetic tokens were labelled observed")
    require(all(group["codex_usage_basis"] == "unknown" and group["cost_basis"] == "unknown" and group["api_estimate_basis"] == "unknown" for group in value["groups"]), "synthetic usage gained an observed cost basis")
    require(all(group["wall_time_basis"] in {"synthetic", "unknown"} for group in value["groups"]), "synthetic wall time was labelled observed")
    luna = next(group for group in value["groups"] if group["strategy"] == "astra_luna")
    require(luna["assigned_tasks"] == 2 and luna["accepted_tasks"] == 2, "accepted-task denominator is wrong")
    require(luna["attempts"] == 3 and luna["total_tokens"] == 6300, "failed/retried work was omitted from token accounting")
    require(luna["worker_events"] == 3, "adaptive worker event accounting is wrong")
    astra_group = next(group for group in value["groups"] if group["strategy"] == "astra_only")
    require(astra_group["review_events"] == 1 and astra_group["event_count"] == 3, "direct no-review task was not represented")
    luna_group = next(group for group in value["groups"] if group["strategy"] == "astra_luna")
    require(luna_group["review_events"] == 1 and luna_group["worker_events"] == 3, "material review and multiple worker events were not counted")
    require("no model-quality" in value["claims"] and "host-usage" in value["claims"], "synthetic fixture emitted a model claim")

    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    with tempfile.TemporaryDirectory(prefix="agent-guild-model-eval-") as directory:
        malformed = Path(directory) / "invalid.jsonl"

        live_without_provenance = json.loads(json.dumps(rows[0]))
        live_without_provenance["evidence_kind"] = "observed_model_run"
        write_rows(malformed, [live_without_provenance])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "target_revision" in rejected.stderr, "observed record without provenance was accepted")

        incoherent_attempts = json.loads(json.dumps(rows[1]))
        incoherent_attempts["attempts"][1]["attempt"] = 3
        write_rows(malformed, [incoherent_attempts])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "sequential" in rejected.stderr, "incoherent attempt accounting was accepted")

        missing_failure = json.loads(json.dumps(rows[1]))
        missing_failure["attempts"][0]["stages"][1]["failure_evidence"] = None
        write_rows(malformed, [missing_failure])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "failure_evidence" in rejected.stderr, "failed event without evidence was accepted")

        wrong_model = json.loads(json.dumps(rows[1]))
        wrong_model["attempts"][0]["stages"][1]["model"] = "gpt-6-astra"
        write_rows(malformed, [wrong_model])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "model/effort mismatch" in rejected.stderr, "worker model distinction was not enforced")

        wrong_role = json.loads(json.dumps(rows[0]))
        wrong_role["attempts"][0]["stages"][1]["role"] = "worker"
        write_rows(malformed, [wrong_role])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "may not record a worker" in rejected.stderr, "strategy role distinction was not enforced")

        wrong_order = json.loads(json.dumps(rows[0]))
        wrong_order["attempts"][0]["stages"][1]["sequence"] = 3
        write_rows(malformed, [wrong_order])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "sequence order" in rejected.stderr, "event order was not enforced")

        duplicate_invocation = json.loads(json.dumps(rows[0]))
        duplicate_invocation["attempts"][0]["stages"][1]["invocation_id"] = duplicate_invocation["attempts"][0]["stages"][0]["invocation_id"]
        write_rows(malformed, [duplicate_invocation])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "duplicate invocation_id" in rejected.stderr, "duplicate invocation was accepted")

        # A root user override is recorded as effective event data and is
        # permitted only when the provenance explicitly marks it.
        manual_rows = json.loads(json.dumps(rows))
        for index, row in enumerate(manual_rows):
            row["evidence_kind"] = "manual_record"
            row["provenance"]["run_id"] = f"manual-{index}"
            for attempt in row["attempts"]:
                attempt["wall_time_source"] = "manual"
                for stage in attempt["stages"]:
                    stage["usage"]["usage_source"] = "manual"
                    stage["usage"]["api_cost_source"] = "unknown"
        override = next(row for row in manual_rows if row["strategy"] == "astra_only" and row["task_id"] == "pilot-boundary-negative")
        override["provenance"]["root_override"] = True
        override["attempts"][0]["stages"][0]["reasoning_effort"] = "xhigh"
        write_rows(malformed, manual_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)

        # This is synthetic shape-only data written to a temporary file. It
        # exercises the observed contract and is never published as a benchmark.
        observed_rows = json.loads(json.dumps(rows))
        for index, row in enumerate(observed_rows):
            row["evidence_kind"] = "observed_model_run"
            row["provenance"].update(
                {
                    "run_id": f"observed-{index}",
                    "session_ref": f"session-{index}",
                    "target_revision": "a" * 40,
                    "codex_version": "codex-test",
                    "config_digest": "sha256:" + "b" * 64,
                    "prompt_bundle_digest": "sha256:" + "c" * 64,
                    "skill_bundle_digest": "sha256:" + "d" * 64,
                    "fresh_context": True,
                    "permission_evidence": f"permission-event-{index}",
                }
            )
        false_observed_rows = json.loads(json.dumps(observed_rows))
        write_rows(malformed, false_observed_rows)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "inconsistent with observed_model_run" in rejected.stderr, "synthetic measurements were relabelled as observed")
        for row in observed_rows:
            for attempt in row["attempts"]:
                attempt["wall_time_source"] = "observed"
                for stage in attempt["stages"]:
                    stage["usage"]["usage_source"] = "observed"
                    stage["usage"]["codex_usage"] = 1
                    stage["usage"]["api_cost_usd"] = 2.5
                    stage["usage"]["api_cost_source"] = "account_reported"
        write_rows(malformed, observed_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        observed_summary = run("--summarize", str(malformed))
        require(observed_summary.returncode == 0, observed_summary.stderr)
        observed_value = json.loads(observed_summary.stdout)
        require(any(group["cost_basis"] == "account_reported" and group["total_cost_usd"] is not None for group in observed_value["groups"]), "observed account cost was not separated in summary")
