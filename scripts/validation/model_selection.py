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
        set(manifest["conditions"]) == {"mixed-luna-v1", "mixed-luna-v2"},
        "current manifest must declare both mixed Luna conditions",
    )
    require(
        set(manifest["profiles"]) == {"solo", "current3", "split3", "flow3", "split4", "split6", "split8"},
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

    mixed_fixture = ROOT / "scripts/validation/fixtures/model_eval_mixed_condition.jsonl"
    mixed_valid = run("--validate-results", str(mixed_fixture))
    require(mixed_valid.returncode == 0, mixed_valid.stderr)
    mixed_summary = run("--summarize", str(mixed_fixture))
    require(mixed_summary.returncode == 0, mixed_summary.stderr)
    mixed_value = json.loads(mixed_summary.stdout)
    require(len(mixed_value["groups"]) == 1, "mixed condition rows were split or merged unexpectedly")
    mixed_group = mixed_value["groups"][0]
    require(
        mixed_group["condition_id"] == "mixed-luna-v1"
        and mixed_group["assigned_tasks"] == 2
        and mixed_group["worker_events"] == 5
        and mixed_group["review_events"] == 1,
        "mixed condition identity or role accounting was lost",
    )
    require(
        mixed_group["named_role_events"] == {
            "adventurer": 1,
            "guildmaster": 2,
            "inquisitor": 1,
            "root": 1,
            "scholar": 2,
            "sentinel": 1,
            "verifier": 1,
        },
        "three GPT-6 Luna roles and the GPT-6 Sol Sentinel were not counted",
    )

    mixed_v2_fixture = ROOT / "scripts/validation/fixtures/model_eval_mixed_v2_condition.jsonl"
    mixed_v2_valid = run("--validate-results", str(mixed_v2_fixture))
    require(mixed_v2_valid.returncode == 0, mixed_v2_valid.stderr)
    mixed_v2_summary = run("--summarize", str(mixed_v2_fixture))
    require(mixed_v2_summary.returncode == 0, mixed_v2_summary.stderr)
    mixed_v2_value = json.loads(mixed_v2_summary.stdout)
    require(len(mixed_v2_value["groups"]) == 1, "mixed v2 rows were split or merged unexpectedly")
    mixed_v2_group = mixed_v2_value["groups"][0]
    require(
        mixed_v2_group["condition_id"] == "mixed-luna-v2"
        and mixed_v2_group["root_model"] == "gpt-6-astra"
        and mixed_v2_group["root_reasoning_effort"] == "high"
        and mixed_v2_group["assigned_tasks"] == 2
        and mixed_v2_group["review_events"] == 1
        and mixed_v2_group["named_role_events"]["inquisitor"] == 1,
        "mixed v2 identity, default Root effort, or review accounting was lost",
    )

    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    mixed_rows = [json.loads(line) for line in mixed_fixture.read_text(encoding="utf-8").splitlines() if line]
    mixed_v2_rows = [json.loads(line) for line in mixed_v2_fixture.read_text(encoding="utf-8").splitlines() if line]
    with tempfile.TemporaryDirectory(prefix="agent-guild-model-eval-") as directory:
        malformed = Path(directory) / "invalid.jsonl"

        combined_rows = json.loads(json.dumps(rows + mixed_rows))
        write_rows(malformed, combined_rows)
        combined_valid = run("--validate-results", str(malformed))
        require(combined_valid.returncode == 0, combined_valid.stderr)
        combined_summary = run("--summarize", str(malformed))
        require(combined_summary.returncode == 0, combined_summary.stderr)
        combined_groups = json.loads(combined_summary.stdout)["groups"]
        legacy_groups = [group for group in combined_groups if group["condition_id"] is None]
        require(legacy_groups == value["groups"], "mixed condition records changed legacy v3 aggregation")
        require(
            [group["condition_id"] for group in combined_groups if group["condition_id"] is not None]
            == ["mixed-luna-v1"],
            "independent condition IDs were merged in summary output",
        )

        both_conditions = json.loads(json.dumps(mixed_rows + mixed_v2_rows))
        write_rows(malformed, both_conditions)
        both_valid = run("--validate-results", str(malformed))
        require(both_valid.returncode == 0, both_valid.stderr)
        both_summary = run("--summarize", str(malformed))
        require(both_summary.returncode == 0, both_summary.stderr)
        both_groups = json.loads(both_summary.stdout)["groups"]
        require(
            [(group["condition_id"], group["root_reasoning_effort"]) for group in both_groups]
            == [("mixed-luna-v1", "high"), ("mixed-luna-v2", "high")],
            "v1/v2 records or their Root effort coverage were combined",
        )

        v2_xhigh_rows = json.loads(json.dumps(mixed_v2_rows))
        for row in v2_xhigh_rows:
            row["evidence_kind"] = "manual_record"
            row["provenance"]["root_override"] = True
            row["provenance"]["run_id"] += "-xhigh"
            for attempt in row["attempts"]:
                attempt["wall_time_source"] = "manual"
                for event in attempt["stages"]:
                    event["invocation_id"] += "-xhigh"
                    event["usage"]["usage_source"] = "manual"
                    event["usage"]["api_cost_source"] = "unknown"
                    if event["role"] == "root":
                        event["reasoning_effort"] = "xhigh"
        write_rows(malformed, v2_xhigh_rows)
        v2_xhigh_valid = run("--validate-results", str(malformed))
        require(v2_xhigh_valid.returncode == 0, v2_xhigh_valid.stderr)
        v2_xhigh_summary = run("--summarize", str(malformed))
        require(v2_xhigh_summary.returncode == 0, v2_xhigh_summary.stderr)
        v2_xhigh_group = json.loads(v2_xhigh_summary.stdout)["groups"][0]
        require(
            v2_xhigh_group["condition_id"] == "mixed-luna-v2"
            and v2_xhigh_group["root_model"] == "gpt-6-astra"
            and v2_xhigh_group["root_reasoning_effort"] == "xhigh"
            and v2_xhigh_group["evidence_kind"] == "manual_record",
            "v2 Root xhigh override was not accepted and grouped independently",
        )

        for condition_rows, field, invalid_value, message in (
            (mixed_rows, "reasoning_effort", "max", "reasoning_effort must be one of ['xhigh']"),
            (mixed_v2_rows, "reasoning_effort", "xhigh", "reasoning_effort must be one of ['max']"),
            (mixed_v2_rows, "role", "worker", "accounting role must be review"),
        ):
            invalid_condition = json.loads(json.dumps(condition_rows))
            target = next(
                event
                for attempt in invalid_condition[0]["attempts"]
                for event in attempt["stages"]
                if event.get("named_role") == "inquisitor"
            )
            target[field] = invalid_value
            write_rows(malformed, invalid_condition)
            rejected = run("--validate-results", str(malformed))
            require(
                rejected.returncode == 2 and message in rejected.stderr,
                f"invalid {condition_rows[0]['condition_id']} Inquisitor {field} was accepted",
            )

        manual_high_effort_rows = json.loads(json.dumps(mixed_rows))
        for row in manual_high_effort_rows:
            row["evidence_kind"] = "manual_record"
            row["provenance"]["run_id"] = "manual-" + row["provenance"]["run_id"]
            for attempt in row["attempts"]:
                attempt["wall_time_source"] = "manual"
                for event in attempt["stages"]:
                    event["invocation_id"] += "-manual"
                    event["usage"]["usage_source"] = "manual"
                    event["usage"]["api_cost_source"] = "unknown"
        selected_effort_rows = json.loads(json.dumps(manual_high_effort_rows))
        for row in selected_effort_rows:
            row["provenance"]["root_override"] = True
            row["provenance"]["run_id"] += "-ultra"
            for attempt in row["attempts"]:
                for event in attempt["stages"]:
                    event["invocation_id"] += "-ultra"
                    if event["role"] == "root":
                        event["reasoning_effort"] = "ultra"
        write_rows(malformed, manual_high_effort_rows + selected_effort_rows)
        selected_valid = run("--validate-results", str(malformed))
        require(selected_valid.returncode == 0, selected_valid.stderr)
        selected_summary = run("--summarize", str(malformed))
        require(selected_summary.returncode == 0, selected_summary.stderr)
        selected_groups = json.loads(selected_summary.stdout)["groups"]
        require(
            {group["root_reasoning_effort"] for group in selected_groups} == {"high", "ultra"}
            and all(group["condition_id"] == "mixed-luna-v1" for group in selected_groups),
            "Root effort selection was not recorded and grouped independently",
        )

        split_effort_coverage = json.loads(json.dumps(manual_high_effort_rows))
        ultra_boundary = next(
            row for row in split_effort_coverage if row["task_id"] == "pilot-boundary-negative"
        )
        ultra_boundary["provenance"]["root_override"] = True
        ultra_boundary["provenance"]["run_id"] += "-ultra-only"
        for attempt in ultra_boundary["attempts"]:
            for event in attempt["stages"]:
                event["invocation_id"] += "-ultra-only"
                if event["role"] == "root":
                    event["reasoning_effort"] = "ultra"
        write_rows(malformed, split_effort_coverage)
        rejected = run("--validate-results", str(malformed))
        require(
            rejected.returncode == 2
            and "mixed-luna-v1 result matrix is incomplete" in rejected.stderr
            and "Root gpt-6-astra/" in rejected.stderr,
            "different Root efforts combined partial task coverage",
        )

        missing_root_override = json.loads(json.dumps(mixed_rows))
        missing_root_override[0]["attempts"][0]["stages"][0]["reasoning_effort"] = "xhigh"
        write_rows(malformed, missing_root_override)
        rejected = run("--validate-results", str(malformed))
        require(
            rejected.returncode == 2 and "requires provenance.root_override" in rejected.stderr,
            "non-default Root effort without override provenance was accepted",
        )

        incomplete_condition = json.loads(json.dumps(mixed_rows[:1]))
        write_rows(malformed, incomplete_condition)
        rejected = run("--validate-results", str(malformed))
        require(
            rejected.returncode == 2 and "mixed-luna-v1 result matrix is incomplete" in rejected.stderr,
            "condition coverage was filled by legacy or missing task rows",
        )

        undeclared_condition = json.loads(json.dumps(mixed_rows))
        undeclared_condition[0]["condition_id"] = "unregistered-condition"
        write_rows(malformed, undeclared_condition)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "condition_id is undeclared" in rejected.stderr, "unknown condition_id was accepted")

        for named_role, field, invalid_value, message in (
            ("sentinel", "model", "gpt-6-luna", "model must remain gpt-6-sol"),
            ("sentinel", "reasoning_effort", "max", "reasoning_effort"),
            ("inquisitor", "model", "gpt-6-sol", "model must remain gpt-6-astra"),
            ("inquisitor", "reasoning_effort", "high", "reasoning_effort"),
            ("guildmaster", "model", "gpt-6-luna", "model must remain gpt-6-astra"),
            ("guildmaster", "reasoning_effort", "none", "reasoning_effort"),
        ):
            invalid_condition = json.loads(json.dumps(mixed_rows))
            target = next(
                event
                for attempt in invalid_condition[0]["attempts"]
                for event in attempt["stages"]
                if event.get("named_role") == named_role
            )
            target[field] = invalid_value
            write_rows(malformed, invalid_condition)
            rejected = run("--validate-results", str(malformed))
            require(rejected.returncode == 2 and message in rejected.stderr, f"invalid {named_role} {field} was accepted")

        no_condition_manifest = json.loads(json.dumps(manifest))
        no_condition_manifest.pop("conditions")
        no_condition_manifest_path = Path(directory) / "no-condition-manifest.yaml"
        no_condition_manifest_path.write_text(json.dumps(no_condition_manifest, ensure_ascii=False), encoding="utf-8")
        rejected = run(
            "--manifest",
            str(no_condition_manifest_path),
            "--validate-results",
            str(mixed_fixture),
        )
        require(rejected.returncode == 2 and "condition_id is undeclared" in rejected.stderr, "legacy manifest accepted a condition it does not declare")

        v1_only_manifest = json.loads(json.dumps(manifest))
        v1_only_manifest["conditions"] = {"mixed-luna-v1": manifest["conditions"]["mixed-luna-v1"]}
        v1_only_manifest_path = Path(directory) / "v1-only-manifest.yaml"
        v1_only_manifest_path.write_text(json.dumps(v1_only_manifest, ensure_ascii=False), encoding="utf-8")
        v1_only_valid = run("--manifest", str(v1_only_manifest_path), "--validate-results", str(mixed_fixture))
        require(v1_only_valid.returncode == 0, v1_only_valid.stderr)
        rejected = run("--manifest", str(v1_only_manifest_path), "--validate-results", str(mixed_v2_fixture))
        require(rejected.returncode == 2 and "condition_id is undeclared" in rejected.stderr, "v1-only manifest accepted v2 rows")

        v2_only_manifest = json.loads(json.dumps(manifest))
        v2_only_manifest["conditions"] = {"mixed-luna-v2": manifest["conditions"]["mixed-luna-v2"]}
        v2_only_manifest_path = Path(directory) / "v2-only-manifest.yaml"
        v2_only_manifest_path.write_text(json.dumps(v2_only_manifest, ensure_ascii=False), encoding="utf-8")
        v2_only_valid = run("--manifest", str(v2_only_manifest_path), "--validate-results", str(mixed_v2_fixture))
        require(v2_only_valid.returncode == 0, v2_only_valid.stderr)
        rejected = run("--manifest", str(v2_only_manifest_path), "--validate-results", str(mixed_fixture))
        require(rejected.returncode == 2 and "condition_id is undeclared" in rejected.stderr, "v2-only manifest accepted v1 rows")

        retry_same_condition = json.loads(json.dumps(mixed_rows))
        duplicate_condition_row = json.loads(json.dumps(retry_same_condition[0]))
        duplicate_condition_row["run_id"] = duplicate_condition_row["provenance"]["run_id"]
        duplicate_condition_row["provenance"]["run_id"] += "-duplicate"
        for attempt in duplicate_condition_row["attempts"]:
            for event in attempt["stages"]:
                event["invocation_id"] += "-duplicate"
        retry_same_condition.append(duplicate_condition_row)
        write_rows(malformed, retry_same_condition)
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "duplicate task/strategy result" in rejected.stderr, "duplicate condition/run result was accepted")

        legacy_manifest = json.loads(json.dumps(manifest))
        legacy_manifest["profiles"].pop("flow3")
        legacy_manifest.pop("conditions")
        legacy_manifest_path = Path(directory) / "legacy-manifest.yaml"
        legacy_manifest_path.write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")
        legacy_valid = run(
            "--manifest",
            str(legacy_manifest_path),
            "--validate-results",
            str(fixture),
        )
        require(legacy_valid.returncode == 0, legacy_valid.stderr)
        legacy_summary = run(
            "--manifest",
            str(legacy_manifest_path),
            "--summarize",
            str(fixture),
        )
        require(legacy_summary.returncode == 0, legacy_summary.stderr)
        require(json.loads(legacy_summary.stdout) == value, "legacy six-profile manifest changed fixture summary")

        no_profiles_manifest = json.loads(json.dumps(manifest))
        no_profiles_manifest.pop("profiles")
        no_profiles_manifest_path = Path(directory) / "no-profiles-manifest.yaml"
        no_profiles_manifest_path.write_text(json.dumps(no_profiles_manifest, ensure_ascii=False), encoding="utf-8")
        no_profiles_valid = run(
            "--manifest",
            str(no_profiles_manifest_path),
            "--validate-results",
            str(fixture),
        )
        require(no_profiles_valid.returncode == 0, no_profiles_valid.stderr)

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

        # New additive metrics keep legacy rows valid while counting every
        # attempt, including retries and a terminal failure.
        timed_rows = json.loads(json.dumps(rows))
        for row in timed_rows:
            miss_count = (
                1 if row["strategy"] == "astra_luna" and row["task_id"] == "pilot-installer-conflict"
                else 2 if row["strategy"] == "astra_luna"
                else 0 if row["task_id"] == "pilot-installer-conflict"
                else None
            )
            row["major_miss_count"] = miss_count
            for attempt_index, attempt in enumerate(row["attempts"], 1):
                attempt["timing_breakdown"] = {
                    "dependency_wait_seconds": 0.0,
                    "handoff_seconds": 1.0,
                    "rework_seconds": None if attempt_index == 1 and row["strategy"] == "astra_luna" else 1.0,
                    "source": "synthetic",
                }
        write_rows(malformed, timed_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        timed_summary = run("--summarize", str(malformed))
        require(timed_summary.returncode == 0, timed_summary.stderr)
        timed_value = json.loads(timed_summary.stdout)
        timed_luna = next(
            group for group in timed_value["groups"]
            if group["strategy"] == "astra_luna"
        )
        timed_astra = next(
            group for group in timed_value["groups"]
            if group["strategy"] == "astra_only"
        )
        require(
            timed_luna["first_attempt_accepted_tasks"] == 1
            and timed_luna["first_attempt_acceptance_rate"] == 0.5,
            "first-attempt acceptance ignored retries",
        )
        require(
            timed_luna["task_wall_time_seconds"] == {
                "known_count": 2,
                "unknown_count": 0,
                "median": 6.0,
                "p90": 10.0,
                "max": 10.0,
                "p90_method": "nearest_rank",
                "basis": "synthetic",
            },
            "task wall distribution did not sum all attempts",
        )
        require(
            timed_luna["timing_breakdown"]["dependency_wait_seconds"] == 0.0
            and timed_luna["timing_breakdown"]["dependency_wait_seconds_basis"] == "synthetic"
            and timed_luna["timing_breakdown"]["handoff_seconds"] == 3.0
            and timed_luna["timing_breakdown"]["rework_seconds"] is None
            and timed_luna["timing_breakdown"]["rework_seconds_basis"] == "unknown",
            "timing categories lost zero or missing provenance",
        )
        require(
            timed_luna["major_miss_count"] == 3
            and timed_luna["major_miss_count_basis"] == "synthetic"
            and timed_astra["major_miss_count"] is None
            and timed_astra["major_miss_count_basis"] == "unknown",
            "major miss totals did not propagate unknowns",
        )

        explicit_zero = json.loads(json.dumps(timed_rows))
        for row in explicit_zero:
            if row["strategy"] == "astra_only":
                row["major_miss_count"] = 0
        write_rows(malformed, explicit_zero)
        explicit_zero_summary = run("--summarize", str(malformed))
        require(explicit_zero_summary.returncode == 0, explicit_zero_summary.stderr)
        explicit_zero_value = json.loads(explicit_zero_summary.stdout)
        explicit_zero_group = next(group for group in explicit_zero_value["groups"] if group["strategy"] == "astra_only")
        require(
            explicit_zero_group["major_miss_count"] == 0
            and explicit_zero_group["major_miss_count_basis"] == "synthetic",
            "explicit zero major misses became unknown",
        )

        for invalid_count in (True, -1, 1.5, "1"):
            invalid_major_miss = json.loads(json.dumps(rows[1]))
            invalid_major_miss["major_miss_count"] = invalid_count
            write_rows(malformed, [invalid_major_miss])
            rejected = run("--validate-results", str(malformed))
            require(
                rejected.returncode == 2 and "major_miss_count" in rejected.stderr,
                "invalid major miss count was accepted",
            )

        missing_wall = json.loads(json.dumps(rows))
        missing_wall[1]["attempts"][0]["wall_time_seconds"] = None
        missing_wall[1]["attempts"][0]["wall_time_source"] = "unknown"
        write_rows(malformed, missing_wall)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        missing_wall_summary = run("--summarize", str(malformed))
        require(missing_wall_summary.returncode == 0, missing_wall_summary.stderr)
        missing_wall_value = json.loads(missing_wall_summary.stdout)
        missing_luna = next(group for group in missing_wall_value["groups"] if group["strategy"] == "astra_luna")
        require(
            missing_luna["task_wall_time_seconds"] == {
                "known_count": 1,
                "unknown_count": 1,
                "median": None,
                "p90": None,
                "max": None,
                "p90_method": "nearest_rank",
                "basis": "unknown",
            },
            "missing task timing was silently dropped from cohort quantiles",
        )

        # A failed final attempt remains in the all-attempt task-time total.
        terminal_failure = json.loads(json.dumps(rows))
        terminal_row = next(row for row in terminal_failure if row["strategy"] == "astra_luna" and row["task_id"] == "pilot-installer-conflict")
        terminal_row["provenance"]["run_id"] = "synthetic-terminal-failure"
        terminal_row["accepted"] = False
        for item in terminal_row["acceptance_evidence"]:
            item["passed"] = False
        terminal_attempt = terminal_row["attempts"][-1]
        terminal_attempt["accepted"] = False
        terminal_attempt["wall_time_seconds"] = 11.0
        failed_stage = terminal_attempt["stages"][1]
        failed_stage["status"] = "failed"
        failed_stage["failure_evidence"] = "合成テストデータ上の最終試行失敗"
        write_rows(malformed, terminal_failure)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        terminal_summary = run("--summarize", str(malformed))
        require(terminal_summary.returncode == 0, terminal_summary.stderr)
        terminal_value = json.loads(terminal_summary.stdout)
        terminal_luna = next(group for group in terminal_value["groups"] if group["strategy"] == "astra_luna")
        require(
            terminal_luna["accepted_tasks"] == 1
            and terminal_luna["task_wall_time_seconds"]["max"] == 14.0,
            "terminal failure or slow retry was omitted from task timing",
        )

        # Ten unequal task totals distinguish nearest-rank p90 from always
        # selecting the maximum.
        p90_rows = [row for row in json.loads(json.dumps(rows)) if row["strategy"] == "astra_only"]
        luna_rows = [row for row in rows if row["strategy"] == "astra_luna"]
        for clone_index, (installer_total, boundary_total) in enumerate(zip((10, 20, 30, 40, 50), (1, 2, 3, 4, 5))):
            for source_row, total in zip(luna_rows, (installer_total, boundary_total)):
                clone = json.loads(json.dumps(source_row))
                clone["run_id"] = f"p90-{clone_index}-{clone['task_id']}"
                clone["provenance"]["run_id"] = f"p90-provenance-{clone_index}-{clone['task_id']}"
                for attempt_index, attempt in enumerate(clone["attempts"]):
                    attempt["wall_time_seconds"] = (
                        total
                        if len(clone["attempts"]) == 1
                        else total * (0.4 if attempt_index == 0 else 0.6)
                    )
                    for event in attempt["stages"]:
                        event["invocation_id"] += f"-p90-{clone_index}-{attempt_index}"
                p90_rows.append(clone)
        write_rows(malformed, p90_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        p90_summary = run("--summarize", str(malformed))
        require(p90_summary.returncode == 0, p90_summary.stderr)
        p90_value = json.loads(p90_summary.stdout)
        p90_luna = next(group for group in p90_value["groups"] if group["strategy"] == "astra_luna")
        require(
            p90_luna["task_wall_time_seconds"]["known_count"] == 10
            and p90_luna["task_wall_time_seconds"]["p90"] == 40.0
            and p90_luna["task_wall_time_seconds"]["max"] == 50.0,
            "nearest-rank p90 was not distinguished from the maximum",
        )

        # flow3 shares split3's full named-role set while current3 remains
        # restricted to its historical roles.
        flow_rows = json.loads(json.dumps(profiled_rows))
        flow_rows = [row for row in flow_rows if row["strategy"] == "astra_luna"]
        for row_index, row in enumerate(flow_rows):
            row["profile"] = "flow3"
            row["run_id"] = f"flow3-{row_index}"
            row["provenance"]["run_id"] = f"flow3-provenance-{row_index}"
            worker_index = 0
            for attempt in row["attempts"]:
                for event in attempt["stages"]:
                    event["invocation_id"] += f"-flow3-{row_index}"
                    if event["role"] == "worker":
                        event["named_role"] = ("verifier", "sentinel", "adventurer")[worker_index % 3]
                        worker_index += 1
        write_rows(malformed, flow_rows)
        accepted = run("--validate-results", str(malformed))
        require(accepted.returncode == 0, accepted.stderr)
        rejected = run(
            "--manifest",
            str(legacy_manifest_path),
            "--validate-results",
            str(malformed),
        )
        require(
            rejected.returncode == 2 and "profile is unsupported: flow3" in rejected.stderr,
            "flow3 rows were accepted by a manifest that does not declare flow3",
        )

        # Explicit timing values must remain finite, non-negative, and source
        # consistent; a known duration may not exceed known wall time.
        invalid_timing_cases = []
        for metric, value in (("dependency_wait_seconds", True), ("handoff_seconds", -1.0), ("rework_seconds", float("inf"))):
            invalid = json.loads(json.dumps(rows[1]))
            invalid["attempts"][0]["timing_breakdown"] = {
                "dependency_wait_seconds": 0.0,
                "handoff_seconds": 0.0,
                "rework_seconds": 0.0,
                "source": "synthetic",
            }
            invalid["attempts"][0]["timing_breakdown"][metric] = value
            invalid_timing_cases.append(invalid)
        for invalid in invalid_timing_cases:
            write_rows(malformed, [invalid])
            rejected = run("--validate-results", str(malformed))
            require(rejected.returncode == 2 and "timing_breakdown" in rejected.stderr, "invalid timing value was accepted")

        invalid_source = json.loads(json.dumps(rows[1]))
        invalid_source["attempts"][0]["timing_breakdown"] = {
            "dependency_wait_seconds": 0.0,
            "handoff_seconds": 0.0,
            "rework_seconds": 0.0,
            "source": [],
        }
        write_rows(malformed, [invalid_source])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "source is invalid" in rejected.stderr, "unhashable timing source was not rejected cleanly")

        source_violation = json.loads(json.dumps(rows[1]))
        source_violation["attempts"][0]["timing_breakdown"] = {
            "dependency_wait_seconds": 0.0,
            "handoff_seconds": 0.0,
            "rework_seconds": 0.0,
            "source": "observed",
        }
        write_rows(malformed, [source_violation])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "inconsistent with synthetic_fixture" in rejected.stderr, "timing source violation was accepted")

        over_wall = json.loads(json.dumps(rows[1]))
        over_wall["attempts"][0]["timing_breakdown"] = {
            "dependency_wait_seconds": 4.0,
            "handoff_seconds": 0.0,
            "rework_seconds": 0.0,
            "source": "synthetic",
        }
        write_rows(malformed, [over_wall])
        rejected = run("--validate-results", str(malformed))
        require(rejected.returncode == 2 and "exceed known attempt wall time" in rejected.stderr, "timing greater than wall time was accepted")
