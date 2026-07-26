# Guild Quest を複数 Party で進める

広い影響、複数 Party、安全判断、厳密な sequencing が必要な変更を、`guildmaster` の戦略から複数 Party、統合、Trial まで進めるパターンです。
担当数を増やすことではなく、authority と編集境界を全体で一貫させることを目的にします。

## 使う場面

- backend、SDK、管理 UI、運用 docs など複数の責務領域へ影響する
- Party ごとの実装順序や共有契約を先に決める必要がある
- 広い blast radius、security、compatibility、rollback を独立して確認したい
- 一つの Party では context や ownership が過大になる

## 依頼文例

```text
この変更は guild_quest として扱ってください。
target_repo_root は /path/to/guild-root/repositories/example-app です。

目的:
- feature flag の評価契約を backend、SDK、管理 UI で統一する

成功条件:
- 評価結果と fallback の契約が全利用箇所で一致する
- 既存 flag の挙動と運用手順を維持する
- Party ごとの owner、sequencing、rollback、Trial focus が明確

停止条件:
- migration、公開 API 互換性変更、認可、本番設定変更が必要
- Party 境界を越える共有ファイルの owner が決まらない

snapshot:
- revision_id: <Root が確認した HEAD commit SHA>
- kind: revision_only
- scope_paths: 全 Party の owned scope の和集合
- dirty_state: 既存のユーザー変更を除外し、区別できない場合は停止
```

## 期待される流れ

1. Root が `intent_analysis`、success criteria、non-goals、scope、authority、validation、snapshot を持つ Quest Charter を作ります。
2. `guildmaster` が read-only で主要リスク、Party 境界、共有 artifact owner、sequencing、Trial depth、safety gate を設計します。1 Party で十分なら guild quest を正規化する理由を返します。
3. boundary、safety、sequencing の狭い focus で sage を使う場合、`guildmaster` が根拠を再確認し、採用、却下、未解決の disposition を strategy に統合します。
4. Root が strategy と command draft を検証し、各 `captain` へ固有の party ID、owned scope、authority、編集禁止 path、success criteria、snapshot を渡します。下流で Guild Law や authority を広げません。
5. 各`captain`がowned scope、並列化、integration barrier、Party内`artificer`、validation expectations、Trial focusを設計します。
6. 各 Party の adventurer が共通 base revision と owned scope を確認して実装し、base snapshot と Party owned-scope result snapshot を返します。別 Party の scope 変更だけでは stale にせず、base 不一致、scope 重複、同じ scope の後発変更、失敗、未完了があれば global integration barrier を閉じません。
7. 全Party reportが揃った後、編集を停止し、Rootが直接起動したdeployment固定pairのglobal `artificer`だけが共有契約とcross-Party glueを統合します。Root、`guildmaster`、`captain`は実装を引き取りません。
8. global integration report と検証が揃った時点で、全 Party と cross-Party glue を含む `working_tree_content` の integrated snapshot を固定します。以後 Trial 完了まで source state を変更しません。
9. Trial lead の `inquisitor` が stable snapshot に対して `multi_focus_trial`、必要なら `safety_gate` を行います。必要な単一focusだけを、scopeとauthorityを狭め、helper-issued snapshotを親Trialと完全一致させてterminal `examiner`へ委譲し、完了を待ちます。`inquisitor`はlineageとarchitecture、security、compatibility、regression、validationのreportsを根拠確認して統合します。
10. Critical / Major が解消し accept された後、`courier` が strategy、Party / integration handoff、Trial、残リスク、snapshot を Ledger に記録し、Root が final report を返します。

## 完了条件

- Party と共有 artifact の owner が重複せず、sequencing が明確
- Party 内と global の integration barrier が閉じる条件を満たしている
- 各 Party report は共通 base と owned-scope result、global integration / Trial / Ledger は同じ最終 integrated snapshot を参照している
- sage / examiner の根拠確認と finding disposition がある
- safety gate、人間確認、残リスクが下流で失われていない

## 注意点

強い上流判断があっても、曖昧な Party 境界や移動中の source state をモデル能力で補いません。
snapshot、barrier、owner が揃わない場合は Party を増やさず、`needs_human` または strategy の再設計へ戻します。
