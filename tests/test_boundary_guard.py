from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "template/.agents/orchestra/scripts"
sys.path.insert(0, str(SCRIPTS))
import boundary_guard  # noqa: E402
import snapshot_digest  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


class BoundaryGuardTests(unittest.TestCase):
    def make_repo(self, directory: Path) -> Path:
        repo = directory / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        git(repo, "config", "user.name", "Boundary Fixture")
        git(repo, "config", "user.email", "boundary@example.invalid")
        (repo / "src").mkdir()
        (repo / "src/owned.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", "src/owned.txt")
        git(repo, "commit", "--quiet", "-m", "baseline")
        return repo

    def contract(self, repo: Path, snapshot: dict[str, object]) -> dict[str, object]:
        return {
            "type": "task_contract",
            "schema_version": "1.0",
            "id": "contract-boundary-1",
            "target_repo_root": str(repo),
            "allowed_paths": ["src/owned.txt"],
            "owned_paths": ["src/owned.txt"],
            "authority": {"read": True, "edit": True, "validate": True, "local_git": False, "external_actions": False},
            "subject_snapshot": snapshot,
            "expected_outcome": "completed",
            "acceptance_checks": [{"id": "unit-check", "required": True, "description": "unit tests"}],
        }

    def assignment(self, contract: dict[str, object]) -> dict[str, object]:
        return {
            "type": "assignment",
            "schema_version": "1.0",
            "id": "assignment-boundary-1",
            "contract_id": contract["id"],
            "target_repo_root": contract["target_repo_root"],
            "allowed_paths": ["src/owned.txt"],
            "owned_paths": ["src/owned.txt"],
            "authority": {"read": True, "edit": True, "validate": True, "local_git": False, "external_actions": False},
            "subject_snapshot": contract["subject_snapshot"],
            "worker_id": "adventurer",
            "role": "bounded_implementation_owner",
        }

    def result(self, contract: dict[str, object], assignment: dict[str, object], snapshot: dict[str, object]) -> dict[str, object]:
        return {
            "type": "result",
            "schema_version": "1.0",
            "id": "result-boundary-1",
            "contract_id": contract["id"],
            "assignment_id": assignment["id"],
            "target_repo_root": contract["target_repo_root"],
            "base_snapshot": assignment["subject_snapshot"],
            "result_snapshot": snapshot,
            "changed_files": ["src/owned.txt"],
            "outcome": "completed",
            "acceptance_checks": [{"id": "unit-check", "passed": True, "evidence_refs": ["ev-unit"]}],
            "evidence_refs": ["ev-unit"],
            "evidence": [{"id": "ev-unit", "summary": "unit check passed"}],
        }

    def test_result_recomputes_actual_snapshot_and_rejects_stale_self_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("working\n", encoding="utf-8")
            base = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            contract = self.contract(repo, base)
            assignment = self.assignment(contract)
            boundary_guard.validate_task_contract(contract)
            boundary_guard.validate_assignment(assignment, contract)

            (repo / "src/owned.txt").write_text("result\n", encoding="utf-8")
            result_snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            result = self.result(contract, assignment, result_snapshot)
            self.assertEqual(boundary_guard.validate_result(result, assignment, contract)["id"], "result-boundary-1")

            (repo / "src/owned.txt").write_text("attacker\n", encoding="utf-8")
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "actual Git state|stale"):
                boundary_guard.validate_result(result, assignment, contract)

    def test_scope_expansion_and_assignment_overlap_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(repo, kind="revision_only")
            contract = {
                "type": "task_contract",
                "schema_version": "1.0",
                "id": "contract-read-1",
                "target_repo_root": str(repo),
                "allowed_paths": [],
                "owned_paths": [],
                "authority": {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False},
                "subject_snapshot": snapshot,
                "expected_outcome": "completed",
                "acceptance_checks": ["read-check"],
            }
            boundary_guard.validate_task_contract(contract)
            bad_assignment = {
                "type": "assignment",
                "schema_version": "1.0",
                "id": "assignment-read-1",
                "contract_id": contract["id"],
                "target_repo_root": str(repo),
                "allowed_paths": ["src"],
                "owned_paths": ["src"],
                "authority": {"read": True, "edit": False, "validate": True, "local_git": False, "external_actions": False},
                "subject_snapshot": snapshot,
                "worker_id": "adventurer",
                "role": "bounded_implementation_owner",
            }
            with self.assertRaises(boundary_guard.BoundaryError):
                boundary_guard.validate_assignment(bad_assignment, contract)

            # Use content scope for overlap validation so the assignments have
            # a real owned path while still sharing one parent contract.
            (repo / "src/owned.txt").write_text("dirty\n", encoding="utf-8")
            content_snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            content_contract = self.contract(repo, content_snapshot)
            first = self.assignment(content_contract)
            second = deepcopy(first)
            second["id"] = "assignment-boundary-2"
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "重な|scope"):
                boundary_guard.validate_assignment_set([first, second], content_contract)

    def test_review_and_checkpoint_require_matching_evidence_and_findings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            (repo / "src/owned.txt").write_text("reviewed\n", encoding="utf-8")
            snapshot = snapshot_digest.compute_snapshot(repo, kind="working_tree_content", scope_paths=["src/owned.txt"], untracked_paths=[])
            contract = self.contract(repo, snapshot)
            assignment = self.assignment(contract)
            result = self.result(contract, assignment, snapshot)
            finding = {"id": "finding-major", "summary": "must fix", "required": True}
            receipt = {
                "type": "review_receipt",
                "schema_version": "1.0",
                "id": "review-1",
                "contract_id": contract["id"],
                "result_id": result["id"],
                "target_repo_root": str(repo),
                "subject_snapshot": snapshot,
                "decision": "accept",
                "evidence_refs": ["ev-review"],
                "evidence": [{"id": "ev-review", "summary": "review evidence"}],
                "findings": [finding],
                "finding_dispositions": {"adopted": [], "rejected": [], "unresolved": ["finding-major"]},
                "result": result,
                "assignment": assignment,
            }
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "unresolved"):
                boundary_guard.validate_review_receipt(receipt, result, contract)

            checkpoint = {
                "type": "checkpoint",
                "schema_version": "1.0",
                "id": "checkpoint-1",
                "contract_id": contract["id"],
                "target_repo_root": str(repo),
                "subject_snapshot": snapshot,
                "stage": "validated",
                "status": "complete",
            }
            self.assertEqual(boundary_guard.validate_checkpoint(checkpoint, contract, result)["id"], "checkpoint-1")
            mismatch = dict(checkpoint)
            mismatch["subject_snapshot"] = dict(snapshot)
            mismatch["subject_snapshot"]["snapshot_id"] = "sha256:" + "0" * 64
            with self.assertRaises(boundary_guard.BoundaryError):
                boundary_guard.validate_checkpoint(mismatch, contract, result)

    def test_result_and_review_reject_missing_or_dangling_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = self.make_repo(Path(raw))
            snapshot = snapshot_digest.compute_snapshot(
                repo,
                kind="working_tree_content",
                scope_paths=["src/owned.txt"],
                untracked_paths=[],
            )
            contract = self.contract(repo, snapshot)
            assignment = self.assignment(contract)
            result = self.result(contract, assignment, snapshot)

            missing = deepcopy(result)
            missing.pop("evidence")
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "evidence"):
                boundary_guard.validate_result(missing, assignment, contract)

            dangling_check = deepcopy(result)
            dangling_check["acceptance_checks"][0]["evidence_refs"] = ["ev-missing"]
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "evidence_refs"):
                boundary_guard.validate_result(dangling_check, assignment, contract)

            receipt = {
                "type": "review_receipt",
                "schema_version": "1.0",
                "id": "review-dangling",
                "contract_id": contract["id"],
                "result_id": result["id"],
                "target_repo_root": str(repo),
                "subject_snapshot": snapshot,
                "decision": "accept",
                "evidence_refs": ["ev-made-up"],
                "findings": [],
                "finding_dispositions": {"adopted": [], "rejected": [], "unresolved": []},
                "result": result,
                "assignment": assignment,
            }
            with self.assertRaisesRegex(boundary_guard.BoundaryError, "evidence"):
                boundary_guard.validate_review_receipt(receipt, result, contract)


if __name__ == "__main__":
    unittest.main()
