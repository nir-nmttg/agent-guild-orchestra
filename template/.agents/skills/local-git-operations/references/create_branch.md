# ブランチの作成

新規ブランチの作成は、ユーザーが作成を明示した時だけ行います。対象Gitルート、基点の参照、新しい参照、引き継ぐ既存変更、事前・事後の条件を先に固定します。

読み取り専用で現在の参照、状態、detached HEAD・merge・rebaseの状態、基点の存在、ローカル・リモートの同名参照との衝突を確認します。未コミット変更の由来が今回の依頼と結び付かない場合は引き継がず停止します。

新しい参照は目的を表す短いASCII名にし、機密情報、個人情報、長文チケット、内部URLを含めません。`git_guard`の事前確認とスナップショットが一致した時だけ新規参照の作成・切替を行い、既存参照への上書き、強制操作、変更を破棄する切替は使いません。作成後は現在の参照、状態、事後スナップショットを報告します。

この配布の補助スクリプトで、変更のないGitルートからブランチを作る最小例です。`subject_snapshot`には補助スクリプトの出力全体をそのまま使い、ハッシュを手で記入しません。

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
