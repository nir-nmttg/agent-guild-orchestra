"""Offline checks for the honest benchmark accounting harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .core import ROOT, require


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/model_selection_eval.py"), *args],
        text=True,
        capture_output=True,
    )


def validate_model_selection_eval() -> None:
    plan = run("--plan")
    require(plan.returncode == 0, plan.stderr)
    manifest = json.loads(plan.stdout)
    require(set(manifest["strategies"]) == {"astra_only", "astra_luna", "v2_4_baseline"}, "benchmark strategy matrix mismatch")

    fixture = ROOT / "scripts/validation/fixtures/model_eval_offline.jsonl"
    valid = run("--validate-results", str(fixture))
    require(valid.returncode == 0, valid.stderr)
    summary = run("--summarize", str(fixture))
    require(summary.returncode == 0, summary.stderr)
    value = json.loads(summary.stdout)
    require(all(group["evidence_kind"] == "offline_fixture" for group in value["groups"]), "offline evidence label was lost")
    require(all(group["total_cost_usd"] is None for group in value["groups"]), "unknown cost was converted to zero")
    luna = next(group for group in value["groups"] if group["strategy"] == "astra_luna")
    require(luna["assigned_tasks"] == 2 and luna["accepted_tasks"] == 1, "accepted-task denominator excludes a failed task")
    require(luna["attempts"] == 3 and luna["total_tokens"] == 6000, "failed/retried work was omitted from token accounting")
    require("no model-quality or savings evidence" in value["claims"], "offline fixture emitted a model claim")

    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    malformed = fixture.parent / "model_eval_invalid.tmp.jsonl"
    try:
        live_without_provenance = dict(rows[0])
        live_without_provenance["evidence_kind"] = "live_model_run"
        malformed.write_text(json.dumps(live_without_provenance) + "\n", encoding="utf-8")
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "target_revision" in rejected.stderr, "live record without stable provenance was accepted")

        incoherent_attempts = json.loads(json.dumps(rows[1]))
        incoherent_attempts["attempts"][1]["attempt"] = 3
        malformed.write_text(json.dumps(incoherent_attempts) + "\n", encoding="utf-8")
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "sequential" in rejected.stderr, "incoherent attempt accounting was accepted")

        wrong_role = json.loads(json.dumps(rows[0]))
        wrong_role["attempts"][0]["stages"].insert(
            1,
            {
                "role": "worker", "model": "gpt-5.6-luna", "reasoning_effort": "max",
                "status": "completed", "tokens": 1, "cost_usd": None,
            },
        )
        malformed.write_text(json.dumps(wrong_role) + "\n", encoding="utf-8")
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "may not record a worker" in rejected.stderr, "strategy role/model distinction was not enforced")

        wrong_model = json.loads(json.dumps(rows[1]))
        wrong_model["attempts"][0]["stages"][1]["model"] = "gpt-6-astra"
        malformed.write_text(json.dumps(wrong_model) + "\n", encoding="utf-8")
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "model/effort mismatch" in rejected.stderr, "declared strategy model was not enforced")
    finally:
        malformed.unlink(missing_ok=True)
