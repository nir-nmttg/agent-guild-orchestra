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
    require(
        set(manifest["profiles"]) == {"solo", "current3", "split3", "split4", "split6", "split8"},
        "parallel execution profiles are not explicit",
    )
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
    luna = next(group for group in value["groups"] if group["strategy"] == "astra_luna" and group["profile"] == "unknown")
    require(luna["assigned_tasks"] == 2 and luna["accepted_tasks"] == 2, "accepted-task denominator is wrong")
    require(luna["attempts"] == 3 and luna["total_tokens"] == 6300, "failed/retried work was omitted from token accounting")
    require(luna["worker_events"] == 3, "adaptive worker event accounting is wrong")
    astra_group = next(group for group in value["groups"] if group["strategy"] == "astra_only" and group["profile"] == "unknown")
    require(astra_group["review_events"] == 1 and astra_group["event_count"] == 3, "direct no-review task was not represented")
    luna_group = next(group for group in value["groups"] if group["strategy"] == "astra_luna" and group["profile"] == "unknown")
    require(luna_group["review_events"] == 1 and luna_group["worker_events"] == 3, "material review and multiple worker events were not counted")
    require(luna_group["max_open_threads"] is None and luna_group["max_open_threads_basis"] == "unknown", "missing thread measurements were converted to a value")
    require(luna_group["max_parallel_child_turns"] is None and luna_group["max_parallel_child_turns_basis"] == "unknown", "missing turn timings were converted to a value")
    require("no model-quality" in value["claims"] and "host-usage" in value["claims"], "synthetic fixture emitted a model claim")

    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    with tempfile.TemporaryDirectory(prefix="agent-guild-model-eval-") as directory:
        malformed = Path(directory) / "invalid.jsonl"

        # Profiled synthetic records exercise the optional schema without
        # changing the legacy fixture used above.
        profiled_rows = json.loads(json.dumps(rows))
        for row_index, row in enumerate(profiled_rows):
            row["run_id"] = f"profile-run-{row_index}"
            row["profile"] = "solo" if row["strategy"] == "astra_only" else "split3"
            for attempt_index, attempt in enumerate(row["attempts"], 1):
                has_child = any(event["role"] in {"worker", "review"} for event in attempt["stages"])
                attempt["max_open_threads"] = 1 if has_child else 0
                attempt["max_open_threads_source"] = "synthetic"
                base = float(row_index * 100 + attempt_index * 10)
                worker_role_index = 0
                for event_index, event in enumerate(attempt["stages"]):
                    if event["role"] == "root":
                        event["named_role"] = "guildmaster"
                    elif event["role"] == "review":
                        event["named_role"] = "inquisitor"
                    else:
                        event["named_role"] = ("adventurer", "scholar", "adventurer")[worker_role_index % 3]
                        worker_role_index += 1
                    event["start_time"] = base + event_index
                    event["end_time"] = base + event_index + 1
        write_rows(malformed, profiled_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        profiled_summary = run("--summarize", str(malformed))
        require(profiled_summary.returncode == 0, profiled_summary.stderr)
        profiled_value = json.loads(profiled_summary.stdout)
        split3 = next(group for group in profiled_value["groups"] if group["profile"] == "split3")
        require(split3["run_count"] == 2, "profiled run identifiers were not retained")
        require(split3["max_open_threads"] == 1 and split3["max_open_threads_basis"] == "synthetic", "open thread measurements were not aggregated")
        require(split3["max_parallel_child_turns"] == 1 and split3["max_parallel_child_turns_basis"] == "synthetic", "child turn parallelism was not computed")
        require(split3["named_role_events"].get("adventurer") == 2 and split3["named_role_events"].get("scholar") == 1, "named worker roles were not counted")
        require(split3["usage_by_role"]["worker"]["tokens"] == 3300, "worker usage was not separated from total usage")

        # The current three-child condition predates Verifier/Sentinel.  Keep
        # those new roles exclusive to split profiles.
        for invalid_role in ("verifier", "sentinel"):
            current3_role = json.loads(json.dumps(profiled_rows[1]))
            current3_role["profile"] = "current3"
            current3_role["run_id"] = f"current3-invalid-{invalid_role}"
            current3_role["provenance"]["run_id"] = f"current3-invalid-provenance-{invalid_role}"
            current3_role["attempts"][0]["stages"][1]["named_role"] = invalid_role
            write_rows(malformed, [current3_role])
            rejected = run("--validate-results", str(malformed))
            require(rejected.returncode == 2 and "not allowed by profile current3" in rejected.stderr, "current3 accepted a newly added worker role")

        missing_named_role = json.loads(json.dumps(profiled_rows[1]))
        missing_named_role["attempts"][0]["stages"][1].pop("named_role")
        write_rows(malformed, [missing_named_role])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "requires named_role" in rejected.stderr, "explicit profile accepted a missing named role")

        incomplete_profile = json.loads(json.dumps(profiled_rows))
        incomplete_profile.pop(1)
        write_rows(malformed, incomplete_profile)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "split3 result matrix is incomplete" in rejected.stderr, "profile coverage was filled by another profile")

        partial_timing = json.loads(json.dumps(profiled_rows))
        partial_timing[1]["attempts"][1]["stages"][1].pop("start_time")
        partial_timing[1]["attempts"][1]["stages"][1].pop("end_time")
        write_rows(malformed, partial_timing)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, "missing both timestamps on one child discarded the record")
        partial_summary = run("--summarize", str(malformed))
        require(partial_summary.returncode == 0, partial_summary.stderr)
        partial_value = json.loads(partial_summary.stdout)
        partial_group = next(group for group in partial_value["groups"] if group["profile"] == "split3")
        require(partial_group["max_parallel_child_turns"] is None and partial_group["max_parallel_child_turns_basis"] == "unknown", "partial child timing was converted to a parallelism value")

        contradictory_peak = json.loads(json.dumps(profiled_rows))
        child_stages = contradictory_peak[1]["attempts"][1]["stages"]
        child_stages[1]["start_time"], child_stages[1]["end_time"] = 100.0, 103.0
        child_stages[2]["start_time"], child_stages[2]["end_time"] = 101.0, 104.0
        child_stages[3].pop("start_time")
        child_stages[3].pop("end_time")
        write_rows(malformed, contradictory_peak)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "timed child peak" in rejected.stderr, "known thread measurement accepted a contradictory timed peak")

        duplicate_run = json.loads(json.dumps(profiled_rows))
        duplicate_record = json.loads(json.dumps(profiled_rows[1]))
        duplicate_record["provenance"]["run_id"] = "profile-extra-provenance"
        for attempt in duplicate_record["attempts"]:
            for event in attempt["stages"]:
                event["invocation_id"] += "-duplicate"
        duplicate_record["run_id"] = profiled_rows[1]["run_id"]
        duplicate_run.append(duplicate_record)
        write_rows(malformed, duplicate_run)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "duplicate task/strategy result" in rejected.stderr, "duplicate run identifier was accepted")

        wrong_named_role = json.loads(json.dumps(profiled_rows[1]))
        wrong_named_role["attempts"][0]["stages"][1]["named_role"] = "inquisitor"
        write_rows(malformed, [wrong_named_role])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "accounting role" in rejected.stderr, "named role accounting mismatch was accepted")

        invalid_timestamp = json.loads(json.dumps(profiled_rows[1]))
        invalid_timestamp["attempts"][0]["stages"][1]["end_time"] = invalid_timestamp["attempts"][0]["stages"][1]["start_time"]
        write_rows(malformed, [invalid_timestamp])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "end_time must be after" in rejected.stderr, "invalid timestamp ordering was accepted")

        incomplete_timing = json.loads(json.dumps(profiled_rows[1]))
        incomplete_timing["attempts"][0]["stages"][1].pop("end_time")
        write_rows(malformed, [incomplete_timing])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "recorded together" in rejected.stderr, "incomplete timestamp pair was accepted")

        incomplete_threads = json.loads(json.dumps(profiled_rows[1]))
        incomplete_threads["attempts"][0].pop("max_open_threads_source")
        write_rows(malformed, [incomplete_threads])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "measurement is incomplete" in rejected.stderr, "incomplete thread measurement was accepted")

        over_cap = json.loads(json.dumps(profiled_rows[1]))
        over_cap["attempts"][0]["max_open_threads"] = 4
        write_rows(malformed, [over_cap])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "exceeds profile split3 cap" in rejected.stderr, "profile cap overflow was accepted")

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

        quality_retry = json.loads(json.dumps(rows[1]))
        quality_retry["attempts"][0]["stages"][1]["status"] = "completed"
        quality_retry["attempts"][0]["stages"][1]["failure_evidence"] = None
        quality_retry["attempts"][0]["stages"][1]["evidence_refs"] = ["synthetic:quality-failure"]
        quality_retry_rows = json.loads(json.dumps(rows))
        quality_retry_rows[1] = quality_retry
        write_rows(malformed, quality_retry_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, "quality failure before retry was rejected")

        quality_final = json.loads(json.dumps(rows[0]))
        quality_final["provenance"]["run_id"] = "synthetic-quality-final"
        quality_final["accepted"] = False
        quality_final["acceptance_evidence"][0]["passed"] = False
        quality_final["acceptance_evidence"][0]["evidence"] = "synthetic quality failure"
        quality_final["attempts"][0]["accepted"] = False
        quality_final_rows = json.loads(json.dumps(rows))
        quality_final_rows[0] = quality_final
        write_rows(malformed, quality_final_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, "completed invocation with failed rubric was rejected")

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

        # A root effort override is recorded as effective event data and is
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

        wrong_root_model = json.loads(json.dumps(override))
        wrong_root_model["provenance"]["run_id"] = "manual-wrong-root-model"
        wrong_root_model["attempts"][0]["stages"][0]["model"] = "gpt-5.6-luna"
        write_rows(malformed, [wrong_root_model])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "root model must remain" in rejected.stderr, "root model override was accepted")

        mixed_root_effort_rows = json.loads(json.dumps(manual_rows))
        mixed_root_effort = next(row for row in mixed_root_effort_rows if row["strategy"] == "astra_luna")
        mixed_root_effort["provenance"]["root_override"] = True
        mixed_root_effort["attempts"][1]["stages"][0]["reasoning_effort"] = "xhigh"
        write_rows(malformed, mixed_root_effort_rows)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "must remain consistent" in rejected.stderr, "mixed Root effort conditions were accepted")

        write_rows(malformed, manual_rows)
        manual_summary = run("--summarize", str(malformed))
        require(manual_summary.returncode == 0, manual_summary.stderr)
        manual_value = json.loads(manual_summary.stdout)
        root_conditions = {
            (group["strategy"], group["root_model"], group["root_reasoning_effort"])
            for group in manual_value["groups"]
        }
        require(
            ("astra_only", "gpt-6-astra", "high") in root_conditions
            and ("astra_only", "gpt-6-astra", "xhigh") in root_conditions,
            "summary mixed Root effort conditions",
        )

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
