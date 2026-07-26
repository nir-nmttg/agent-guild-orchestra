# Sageの根拠をownerが統合する

`sage`は常設工程ではありません。ownerが具体的な独立focusを特定し、別視点のread-only調査が最終成果を改善すると判断した場合だけ使います。

## 使う場面

- 共有契約の責務重複を独立確認したい
- architecture、security、regressionなど一つのfocusに追加根拠が必要
- ownerの現在の調査と独立して確認できる

focusを切れない、ownerの作業と同じ調査を繰り返す、実装分業が目的の場合は使いません。未使用理由の記録も不要です。

## Assignment

- objectiveと一つのfocus
- read-only authorityとscope
- helper発行snapshot
- 求めるevidenceと停止条件

## 流れ

1. ownerが具体的focusと期待する情報価値を定義します。
2. Rootがdeploymentで固定されたterminal `sage`を直接起動します。
3. Sageはfocus内だけを調べ、findings、evidence、options、risks、unknownsを返します。
4. ownerはevidence refsを確認し、各findingを採用、却下、未解決に分類します。
5. 新しい検証可能なevidenceが見込める場合だけ同じfocusでfollow-upします。
6. scope拡張、人間確認、snapshot変更が必要なら停止します。

## 完了条件

- focus、authority、scope、snapshotがownerを越えていない
- sageとownerのdecision authorityが分離されている
- 採用findingはownerが根拠確認している
- raw discussionではなく判断根拠だけがhandoffされる
