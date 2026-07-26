# Ledger と Git 操作を明示する

作業記録、commit、PR 説明準備などを、実装担当から分けて扱うパターンです。
Guild workflow では、Ledger反映とlocal Git writeは`courier`だけの担当です。assigned read scope内のread-only Gitは全roleが観測できますが、Rootはcontrol-plane確認を超えて対象repoのevidenceを収集しません。

## 使う場面

- 実装後に判断根拠、検証、残リスクを Ledger に残したい
- 未コミット差分を意味のある単位に分けたい
- PR 説明文を branch 差分から作りたい
- 溜まっている commit を秘密情報検査後に GitHub へ push したい
- 実装担当と記録担当を分けたい

## 依頼文例

```text
実装が完了したら、Ledger に残す evidence を整理し、次の local Git 操作だけを courier に割り当ててください。
target_repo_root は /path/to/guild-root/repositories/example-app です。

snapshot:
- revision_id: <Trial が accept した HEAD commit SHA>
- kind: working_tree_content
- scope_paths: src/import/csv_parser.py と tests/test_csv_import.py だけ。untracked file は含めない
- diff_hash: <`working_tree_content` として Trial が accept した上記 path 内容の `agent-guild-orchestra-snapshot-v1` SHA-256。stage 状態は含めない>
- dirty_state: 上記以外の既存変更は保持し、stage しない

Root assignmentで固定する操作:
- Ledger に判断根拠、権限、検証、残リスクを短く残す
- `src/import/csv_parser.py` と `tests/test_csv_import.py` だけを stage する
- staged diff が上記 diff_hash と一致した場合だけ `fix: ignore blank CSV rows` で commit する
- PR 説明文の draft を作る

assignmentの必須境界:
- path/ref scope: `src/import/csv_parser.py` と `tests/test_csv_import.py`、現在branch
- precondition: helper snapshot一致、対象branch、既存stage範囲、無関係差分除外を確認済み
- postcondition: stage/index/worktree/commit後の状態、commit SHA、残差分を確認・報告
- forbidden operation: 以下の禁止操作と外部更新

禁止すること:
- 無関係な差分を stage する
- git reset --hard などの破壊的操作
- secret、raw log、PII を Ledger に残す
- remote push や PR 作成を勝手に行う
```

## 期待される流れ

1. 実装担当が変更点、検証、残リスク、`revision_id` / `diff_hash` を report にまとめます。
2. `inquisitor` が同じ snapshot に対して Trial を行い、完了判断に必要な evidence と finding disposition を補います。
3. Root が Trial -> Ledger / final handoff を確認し、Ledger 記録と local Git 操作を別々の courier action として具体化します。
4. `courier` が snapshot を再確認し、Ledger 用の短い記録を作ります。raw diff、raw log、秘密値、PII は記録しません。
5. Rootが`target_repo_root`、allowlisted operation、path/ref scope、helper-issued snapshot、pre/postcondition、forbidden operationをassignmentへ固定した場合、`courier`が人間のコマンド逐語反復なしに新規branch作成＋切替、origin未push rename、exact stage、index-only exact-path safe unstage、non-amend commitを実行します。最初のGit write直前に同一kind/base/scopeのhelper snapshotとassignment snapshotを完全一致照合し、不一致は`stale_evidence`として停止します。最後のwrite後は別snapshotをpostcondition evidenceとして発行します。pathやhunkを文脈から補わず、allowlist外は一般許可しません。
6. stage 後に、staged path が許可 scope と一致することを別に確認し、同じ path の `working_tree_content` digest を再計算します。digest は stage 状態を含めないため、内容が変わらない stage 前後では同じ値です。不一致や無関係な staged path があれば commit せず停止します。
7. commit 後は commit SHA を新しい `revision_id` として固定し、残る change set の `diff_hash`、`git status`、staged / unstaged / untracked 変更、実行しなかった操作、Ledger event disposition を返します。
8. Ledger、stage、commit の途中で一部だけ成功した場合は、自動 rollback や別経路の再試行をせず、成功済み action と失敗理由を記録します。Root が新しい snapshot と狭い再 assignment を作ります。
9. HEADを動かすreset、hard、worktreeを戻すcheckout/restore、clean、amend、rebase/filter、ref/branch/tag deleteまたはforce move、reflog/prune・復旧困難なgc、破壊的stash、`switch --discard-changes`、`switch -C`、`checkout -B`、`-f`を伴うswitch/checkoutは実行直前の人間確認を挟みます。remote push、PR、Issue、comment、公開、deployも別途実行直前に人間確認を挟みます。
10. GitHub へ push する場合は、`github-safe-push-from-branch` の Push Safety Gate で秘密情報、PII、内部情報、公開禁止情報を弾き、push target、branch / range、公開内容、残リスクを提示して実行直前に人間確認を得ます。

## 完了条件

- Ledger に残す内容が raw log ではなく判断根拠になっている
- commit 単位が作業意図ごとに分かれている
- 無関係なユーザー変更を巻き込んでいない
- GitHub push では Push Safety Gate の結果と残リスクが明示されている
- remote や外部状態を勝手に変更していない
- commit SHA、最終 `git status`、残差分、Ledger / Git action ごとの disposition がある
- Trial が accept した snapshot と stage / commit 対象が一致している
- commit 後の新しい `revision_id` / `diff_hash` が final report に渡されている

## 注意点

Git操作はcourierだけが行い、allowlistの可逆操作も境界固定assignmentなしには進めません。
`必要なら`、`PR ready`、`仕上げて`はtarget、operation、scope、snapshot、pre/postcondition、forbidden operationを欠くため、courierのGit write assignmentにはなりません。
