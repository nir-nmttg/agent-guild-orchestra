# create branch

新規branch作成は、ユーザーが作成を明示した時だけ行います。対象Git root、base ref、new ref、引き継ぐ既存変更、pre/post conditionsを先に固定します。

read-onlyで現在ref、status、detached/merge/rebase状態、base存在、local/remoteの同名ref衝突を確認します。未コミット変更の由来が今回の依頼と結び付かない場合は引き継がず停止します。

new refは目的を表す短いASCII名にし、secret、PII、長文チケット、内部URLを含めません。`git_guard` preflightとsnapshotが一致した時だけ新規refの作成・切替を行い、既存refへの上書き、force、discarding switchは使いません。作成後はcurrent ref、status、post-snapshotを報告します。

この配布のhelperでclean rootからbranchを作る最小例です。`subject_snapshot`はhelper出力全体をそのまま使い、hashを記入しません。

```python
import json, pathlib, subprocess, tempfile

guild_root = pathlib.Path("/absolute/path/to/asked-root")
root = str(guild_root / "repositories/asked_backend")
runtime = guild_root / ".agents/orchestra/scripts"
snapshot = json.loads(subprocess.check_output([
    "python3", str(runtime / "snapshot_digest.py"),
    "--repo", root, "--kind", "revision_only",
], text=True))
contract = {
    "target_repo_root": root,
    "allowed_operations": ["branch_create_and_switch_new"],
    "path_or_ref_scope": {"paths": [], "base_ref": "HEAD", "new_branch": "codex/example-change"},
    "subject_snapshot": snapshot,
}
with tempfile.TemporaryDirectory(prefix="guild-git-") as temporary:
    contract_path = pathlib.Path(temporary) / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    subprocess.run([
        "python3", str(runtime / "git_guard.py"), "apply",
        "--operation", "branch_create_and_switch_new", "--contract", str(contract_path),
    ], check=True)
```
