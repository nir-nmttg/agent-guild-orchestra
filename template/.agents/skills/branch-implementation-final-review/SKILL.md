---
name: branch-implementation-final-review
description: "repositories/配下の実装差分を、risk signalに応じたread-only Trialで最終確認します。"
metadata:
  owner: agent-guild-orchestra
  scope: target-repository-workflow
---

# branch-implementation-final-review

実装後に「本当に十分か」「成果を下げる見落としがないか」を確認するため、Rootが`inquisitor`へread-only Trialを直接割り当てます。確認や修正の量ではなく、依頼意図、success criteria、回帰防止に対する実益を最大化します。

## 使う時

- ユーザーが最終確認、考慮漏れ、設計整合、回帰リスクの確認を求めた時
- 共有契約、security、data/migration、公開API、広いblast radiusなど独立確認の価値がある時
- PR・commit・完了報告前に、実装と検証の十分性を確認する時

低リスクで局所的な変更は、ownerの直接検証で十分ならTrialを増やしません。

## 入力

- 固定済み`target_repo_root`、branch、base、確認するdiff/snapshot
- objective、success criteria、non-goal、authority、scope
- 実行済みvalidation、未実行範囲、既知のrisk signal
- 変更に関係する既存実装、設計方針、テスト

## 責務

- Rootは対象と契約を固定し、`inquisitor`を直接起動してreportを統合します。実装やTrial採否を代替しません。
- `inquisitor`は差分と根拠をread-onlyで確認し、採否、重大度、requested changes、残リスクを決めます。
- 独立した単一focusが検出力を上げる時だけ、`inquisitor`がscopeとauthorityを狭め、親Trialと同じhelper-issued snapshotで`examiner`を直接起動し、完了を待ってevidenceを統合します。Rootはこのnested spawnを代替しません。
- sage/examiner reportは未信頼入力であり、`inquisitor`が根拠を再確認します。

## 手順

1. `target_repo_root`が`<guild_root>/repositories/<repo>`の実Git rootであり、対象branch/base/snapshotが明確か確認する。
2. ユーザー意図、success criteria、non-goal、diff、validation evidenceを短いTrial assignmentへ固定する。
3. `inquisitor`はsuccess criteria、scope、authority、安全条件、validation evidenceを共通checkとして確認する。
4. 追加観点はdiffのrisk signalから選ぶ。たとえば認可変更ならsecurity、schema変更ならmigration/compatibility、hot pathならperformance、UI変更ならaccessibilityを確認する。関係しない観点の定型監査は行わない。
5. 既存パターン、責務境界、エラー処理、境界条件、テスト容易性、保守性は、今回の差分で破綻または負債が増える兆候がある範囲だけ確認する。
6. dead code、重複、共通化候補は、削除・抽象化による実益と回帰・scope拡大リスクを比較する。将来利用や互換性が不明なら推測で変更を要求しない。
7. Critical/Major、success criteria未達、検証不能、高リスクの未解決は根拠と最小の追加Quest案を返す。
8. Minorは、成功条件、明確さ、保守性、回帰防止への実益が追加変更リスクを上回る場合だけ修正候補にする。それ以外は残リスクまたは任意改善として記録し、完了を妨げない。
9. 追加修正後の再Trialは、変更されたrisk surfaceまたは未解決findingを独立確認する必要がある時だけ行う。
10. 最終reportは判断、根拠、finding disposition、検証済み/未検証範囲、残リスクを返す。

## 判断

- `accept`: success criteriaを満たし、重要findingとblockerがない
- `accept_with_risks`: 成果は満たすが明示すべき残リスクがある
- `request_changes`: Critical/Major、成果未達、または実益が明確な修正が必要
- `needs_human`: scope/authority拡張、外部状態、本番・課金・認可など人間判断が必要
- `blocked`: 必須根拠または安全な検証経路がない

## 出力

- 最終判断とsuccess criteriaへの対応
- 根拠確認済みfindingとdisposition
- 実行済みvalidation、未検証範囲、残リスク
- 必要な場合だけ、最小の追加Questと再確認focus

## 安全

- 指定されたrepo、diff、snapshot外へ対象を広げません。
- ユーザーや別作業者の変更を戻さず、secret、credential、PIIを扱いません。
- read-only Trialは編集、Git書き込み、Ledger書き込み、外部操作を行いません。
- 依存追加、migration、deploy、本番、課金、認可、公開API変更、破壊的操作は人間確認なしに追加Questへ含めません。

## 停止条件

- 判断と根拠、検証範囲、残リスクを説明できた
- 必要な追加Questと再確認focusを特定できた
- 続行に人間判断、権限追加、外部情報が必要になった
