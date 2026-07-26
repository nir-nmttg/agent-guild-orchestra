# Wardenで例外診断する

通常のcontrolはownerが行います。`warden`は、矛盾、反復失敗、scope drift、長時間停滞で次の行動を絞れない時だけ使うread-onlyの例外診断です。

## 使う場面

- contradictory evidenceで前提が崩れた
- 同じ領域の検証が反復して失敗する
- scope driftが本来の成果に必要か判断できない
- 長時間作業でblockerと次の最小行動を整理し直したい

単なる不安、自己評価値、通常の一回のtest failureでは起動しません。

## 入力

- objective、success criteria、scope、authority
- 現在の`evidence_state`
- 最初のfailureと実施済み診断
- 固定snapshot

## 禁止

- 実装、編集、採否、Ledger/Git/外部状態更新
- scopeまたはauthorityの拡張
- 別agentの起動

## 流れ

1. ownerがsource mutationを止め、snapshotとevidenceを固定します。
2. Rootがdeploymentで固定されたterminal `warden`を直接起動します。
3. Wardenはblocker、failed check、矛盾、scope drift、高リスクtriggerを区別します。
4. 根拠付きの`recommended_next_action`、停止条件、必要なescalationだけを返します。
5. ownerが根拠を確認し、採用、却下、未解決を決めます。
6. snapshotが変わった場合は古い診断を使いません。

## 完了条件

- 推薦が固定snapshotとevidenceに結び付いている
- ownerとWardenのdecision authorityが分離されている
- 根拠のない修正stackを避け、次の最小行動が明確
- security triggerや人間確認条件が失われていない
