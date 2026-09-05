# create branch

新規branch作成は、ユーザーが作成を明示した時だけ行います。対象Git root、base ref、new ref、引き継ぐ既存変更、pre/post conditionsを先に固定します。

read-onlyで現在ref、status、detached/merge/rebase状態、base存在、local/remoteの同名ref衝突を確認します。未コミット変更の由来が今回の依頼と結び付かない場合は引き継がず停止します。

new refは目的を表す短いASCII名にし、secret、PII、長文チケット、内部URLを含めません。`git_guard` preflightとsnapshotが一致した時だけ新規refの作成・切替を行い、既存refへの上書き、force、discarding switchは使いません。作成後はcurrent ref、status、post-snapshotを報告します。

現在導入されている`git_guard.py`、`snapshot_digest.py`と対応testを確認したうえで、clean rootからbranchを作る最小例は次の形です。`subject_snapshot`はhelper出力全体をそのまま使い、hashを記入しません。

```python
import json, pathlib, subprocess

root = "/absolute/path/to/repository"
runtime = pathlib.Path(root) / ".agents/orchestra/scripts"
snapshot = json.loads(subprocess.check_output([
    "python3", str(runtime / "snapshot_digest.py"),
    "--repo", root, "--kind", "revision_only",
], text=True))
contract = {
    "type": "assignment", "schema_version": "1.0", "id": "branch-create-1",
    "target_repo_root": root,
    "allowed_operations": ["branch_create_and_switch_new"],
    "path_or_ref_scope": {"paths": [], "base_ref": "HEAD", "new_branch": "codex/example-change"},
    "subject_snapshot": snapshot,
    "preconditions": {"target_repo_root_confirmed": True, "preflight_snapshot_matches_assignment": True},
    "postconditions": {},
    "forbidden_operations": ["push", "reset", "commit_amend", "rebase", "clean"],
}
contract_path = pathlib.Path("/tmp/branch-create-contract.json")
contract_path.write_text(json.dumps(contract), encoding="utf-8")
subprocess.run([
    "python3", str(runtime / "git_guard.py"), "apply",
    "--operation", "branch_create_and_switch_new", "--contract", str(contract_path),
], check=True)
```
