# Git guardの使い方

`git_guard`は機械的な境界確認を提供します。この配布に対応する呼び出し方は以下のとおりです。呼び出し仕様が異なる場合は`--help`を確認し、未対応なら停止します。通常の利用のために補助スクリプトの全文や、配布されていない開発用テストを読み直す必要はありません。

## 現在の配布版の呼び出し仕様

このテンプレートの実行補助機構では、まずCLIまたは同じモジュールの`compute_snapshot(...)`でスナップショットを発行します。

補助スクリプトの配置場所は、Git管理外の親の`<guild_root>/.agents/orchestra/scripts/`です。以下のCLIはその絶対パスで呼び出し、`--repo`と操作条件の`target_repo_root`には、操作する子の実Gitルートを渡します。親にGit操作を向けたり、子に補助スクリプトを複製したりしません。

```text
snapshot_digest.py --repo <absolute-git-root> --kind revision_only|working_tree_content|commit_range [--base-ref <ref>] [--head-ref <ref>] [--scope <relative-path>]... [--untracked <relative-path>]...
```

`working_tree_content`には一つ以上の`--scope`が必要で、その範囲内の実際の未追跡パスをすべて`--untracked`へ列挙します。`commit_range`には`--base-ref`と`--head-ref`が必要です。返された正規化済みのデータ全体を証拠として保持し、`snapshot_id`や`diff_hash`だけを作成・置換しません。

通常の引き継ぎ、結果、レビュー、チェックポイントはCodex標準のタスク履歴へ短く記録します。作業指示・結果・レビューの成果物を検証する常駐のスキーマ検証機構はありません。`git_guard`へ渡す作業指示のJSONは、明示されたGit操作の条件としてだけ扱います。

Git書き込みでは、限定された許可一覧から操作を一つだけ選び、作業指示のJSONファイルを渡します。ステージにパッチを使う場合だけ`--patch-file`または`--patch`を追加します。

```text
git_guard.py apply --operation <allowed-operation> --contract <json-file> [--patch-file <patch-file>]
```

操作条件には`target_repo_root`、`allowed_operations`、`path_or_ref_scope`、補助スクリプトが出力した`subject_snapshot`を渡します。コミットには、範囲指定内の`message`と、確認済みの`expected_index_tree`も必要です。作業指示の`type`・`id`、空の`postconditions`、自己申告の真偽値による事前条件は不要です。未対応のフラグや互換用の代替手段を推測しません。

現在の許可一覧は`branch_create_and_switch_new`、`rename_origin_unpushed_branch`、`stage_exact_paths_or_hunks`、`unstage_index_only_exact_paths`、`commit_non_amend`です。`git_guard.py`は成功時に`preflight_snapshot`、`postwrite_snapshot`、簡潔な操作の証拠を返します。パッチ本文やコミットメッセージは返しません。

## 事前確認

- ユーザーまたはメインセッションが明示したGitルートと、実際のGitルートが一致することを確認する。
- 操作、現在・基点・新規の参照、正確なパス・差分箇所、未追跡ファイルの扱い、許可された副作用を作業指示へ固定する。
- detached HEAD、保護された参照、merge・rebase・cherry-pickの途中、既存変更の担当者不明、対象外のパス、機密情報の疑いがある差分を検出したら止める。
- スナップショット補助スクリプトを同じ対象、種別、基点・参照、範囲で使い、作業指示の事前スナップショットと一致する証拠だけを採用する。不一致は`stale_evidence`として扱い、ダイジェストを手計算しない。

## 書き込みの境界

許可するローカル操作は、今回明示されたものに限ります。通常の許可対象は、新規ブランチの作成・切替、originへ未pushと確認済みのブランチの改名、正確なパス・差分箇所のステージ、指定パスのindexだけのステージ解除、既存コミットを修正しない通常のコミットです。既存ブランチへの一般的な切替や、変更を破棄する操作は含めません。

`git_guard`、サンドボックス、承認、ユーザー指示が別々に示す権限を足し合わせて範囲を広げません。補助スクリプトが拒否したら、別コマンドや別パスへ迂回せず停止します。

`working_tree_content`スナップショットは作業ツリーの内容と範囲を結び付けます。コミット準備では次のCLI、または`git_guard.index_tree(target_repo_root)`からindexのツリーOIDを取得します。

```text
git_guard.py index-tree --repo <absolute-git-root>
```

これは`git write-tree`によるGit書き込みの準備です。作業ツリーの内容は変更しませんが、オブジェクトDBやindexキャッシュへ書き込むことがあるため、純粋な読み取り専用の調査では実行しません。返された`expected_index_tree`とスナップショットの`revision_id`の差分を、補助スクリプトの安全なGit実行経路（`snapshot_digest.run_git`で`diff --no-ext-diff --no-textconv --ignore-submodules=none <revision_id> <expected_index_tree> -- <owned paths>`）で確認します。変わり得る現在のindexだけを見て、別のツリーを承認したことにしません。

確認したOIDを操作条件の`expected_index_tree`へそのまま渡します。ガードはindex照合後もその固定ツリーからコミットを作り、期待する旧HEADを照合して参照を更新します。改名は変更元・変更先の両方を範囲へ含めます。競合で失敗した場合は状況を読み直し、未確認ツリーへの差し替えや自動復元はしません。

## 事後確認

書き込み後は現在の参照、状態、意図したパス・index・コミットの状態、外部リモートが変更されていないことを確認します。補助スクリプトで事後条件の証拠となるスナップショットを別に発行します。事前と同じダイジェストでも実行回数の証明とは扱いません。変更が想定外なら追加の書き込みをせず、メインセッションへ返します。

## 許可しない操作

push、PR、Issue、コメント、マージ、リリース、デプロイ、reset、restore、clean、amend、rebase・履歴のフィルター処理、ブランチ・参照・タグの削除や強制移動、破壊的なstash、機密情報・個人情報の読み書きは、この参照文書で許可されません。
