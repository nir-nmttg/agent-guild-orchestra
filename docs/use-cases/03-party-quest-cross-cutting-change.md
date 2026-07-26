# 横断変更を分業する

複数領域にまたがる変更を、担当範囲と Trial focus を分けて進めるパターンです。
設計、実装、検証、レビューを同じ担当に寄せすぎないために使います。

## 使う場面

- backend、frontend、docs、tests など複数領域にまたがる
- 既存契約を壊さないか独立確認したい
- 仕様は決まっているが、実装箇所が複数ある
- regression、security、validation など別 focus の Trial が有効

## 依頼文例

```text
この変更は party_quest として扱ってください。
target_repo_root は /path/to/guild-root/repositories/example-app です。

目的:
- ユーザー設定の通知チャネルを email / slack / none から選べるようにする

成功条件:
- 設定画面で通知チャネルを保存できる
- backend validation と frontend 表示が一致する
- 既存ユーザーの初期値が明確
- 関連 docs とテストが更新される

party_tactics:
- 実装担当は既存設計を読んで最小差分で進める
- assignment ごとに assignment_id、owned scope、編集禁止 path、validation expectation を持たせる
- captainがdeploymentで固定された`artificer`用assignmentとintegration barrierを設計する
- Trial は validation、UI 回帰、既存データ影響を focus にする
- 必要なら read-only sage を 1 focus だけ使う

snapshot:
- revision_id: <Root が確認した HEAD commit SHA>
- kind: revision_only
- scope_paths: 全 assignment の owned scope の和集合
- dirty_state: 既存のユーザー変更を assignment scope から除外し、区別できない場合は停止

停止条件:
- migration が必要
- 外部サービス送信の実動作確認が必要
- 認可や課金に影響する
```

## 期待される流れ

1. Root が Quest Charter と共通の `base_snapshot` を作ります。clean な開始点は `revision_only` とし、全 worker が同じ `revision_id` を参照します。
2. `captain` が担当範囲、assignment identity、並列化可否、sequencing、Trial focus、integration barrier を設計します。同じファイルだけでなく、生成物、lockfile、schema、共有契約、共通 test fixture の owner も一つに固定します。
3. `captain`はcross-scope調整を行う専用`artificer` assignmentを明示します。`captain`自身は実装を引き取りません。
4. 各`adventurer`が共通base revisionと自分のowned scopeを確認し、scope内で実装します。reportには変更、validation evidence、変化した`evidence_state`、残リスク、base snapshot、owned scopeの`result_snapshot`を含めます。
5. 全実装 report が揃うまで integration barrier を閉じません。base revision 不一致、owned scope 重複、同じ owned scope の後発変更、失敗、未完了 assignment が一つでもあれば Trial へ進みません。
6. Root と `captain` が handoff を確認した後、並列編集を停止し、`artificer` だけが cross-scope glue、共有契約、統合検証を担当します。新しい実装が不要なら、その理由を残します。
7. artificer の report 後に全 owned scope と glue を含む `working_tree_content` の integrated snapshot を固定し、以後 Trial 完了まで実装担当は編集しません。変更が生じた場合は barrier を開き直し、新しい integrated snapshot を作ります。
8. `inquisitor` が stable snapshot に対して risk-based に `focused_trial` または `multi_focus_trial` を行います。
9. `inquisitor`がrisk-triggeredに独立focusを選び、必要な場合だけscopeとauthorityを狭め、helper-issued snapshotを親Trialと完全一致させたassignmentでterminal `examiner`を起動します。完了を待ってlineageとevidenceを検証し、複数reviewer時だけfocus分割を記録します。
10. Findings は重大度と disposition を付けて統合され、accept 後に `courier` が snapshot、検証、残リスクを Ledger に記録します。

## 完了条件

- 担当ごとの owned scope が明確
- 同じファイルを複数担当が同時に編集していない
- 共有 artifact と cross-scope glue の owner が一つに決まり、integration barrier 後に stable snapshot が固定されている
- Trial focus と検証結果が成功条件に対応している
- Critical / Major の不足が残っていない
- assignment report は共通 base snapshot と各 owned-scope result snapshot、integration report / Trial / Ledger は同じ最終 integrated snapshot を参照している

## 注意点

`party_quest` は人数を増やすこと自体が目的ではありません。
risk、coupling、blast radius、validation resultを見て、成果の検出力を上げる担当とreviewerだけを置きます。
並列数を増やしても integration barrier と stable snapshot を省略せず、Trial 中に source state を動かしません。
