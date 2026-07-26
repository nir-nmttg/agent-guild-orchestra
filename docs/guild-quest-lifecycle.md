# Guild Quest Lifecycle

```text
Request
  -> risk-adaptive intake
  -> task contract when material
  -> Root direct assignments
  -> bounded execution
  -> Root evidence gate and next action
  -> integration barrier when parallel mutation
  -> risk-triggered Trial
  -> Ledger / final outcome
```

## Rank

| Rank | 適用 | Owner |
| --- | --- | --- |
| `mapmaking` | 未知領域のread-only調査 | `cartographer` |
| `errand` | 明白なbounded作業 | `adventurer` |
| `solo_quest` | 単一ownerの実装 | `adventurer` |
| `party_quest` | 複数owned scope | `captain` |
| `guild_quest` | 複数Partyと広域戦略 | `guildmaster` |

Rankは儀式を決めるものではなく、scope、owner、integration、Trialの判断材料です。

## Execution

- `captain`は共有artifactのowner、順序、owned scope、integration barrierを決めます。
- 各`adventurer`は一つのbounded scopeだけを変更します。
- 全resultが揃いmutationを止めた後、`artificer`がcross-scope glueと統合検証を担当します。
- Rootはtarget・authority・snapshot・queueのcontrol-plane確認、routing、待機、evidence gate、次action、最終synthesisに加え、roleが仕様化したbrowser-control toolだけを実行して観測事実を記録します。対象repoの探索、コード読解、実装、検証、browserの計画/許可操作仕様化/根拠解釈、debug、review evidence収集を引き取りません。
- strategy roleとreview roleも実装を引き取りません。

## Evidence state

数値confidenceではなく、blocker、failed check、verification、scope drift、高リスクtriggerを使います。変化のない状態をhandoffごとに再記述しません。

## Trial

Trialはrisk-triggeredです。低リスクbounded変更はowner validation、高リスク・共有契約・互換性・security・migration・検証失敗は`inquisitor`の独立Trialへ進みます。

`examiner`は必要な単一focusだけを確認します。複数reviewerを使う場合はfocusを重複させず、`inquisitor`がevidenceとfinding dispositionを統合します。

## Safety

すべてのRankでtarget repo境界、secret/PII禁止、state-change authorization、snapshot fail-closedを維持します。
