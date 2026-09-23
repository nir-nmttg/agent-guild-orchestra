# GPT-6 Luna / Solへの移行

## モデルと役割

| 役割 | 移行後のモデル | 推論レベル |
| --- | --- | --- |
| Guildmaster / Root | `gpt-6-astra` | 通常`xhigh`推奨。利用者の選択を優先し、配布設定では固定しない |
| Scholar | `gpt-6-luna` | `max` |
| Adventurer | `gpt-6-luna` | `max` |
| Verifier | `gpt-6-luna` | `max` |
| Sentinel | `gpt-6-sol` | `xhigh` |
| Inquisitor | `gpt-6-astra` | `max` |

既定のsubagentは`gpt-6-luna/max`です。SentinelとInquisitorは名前付き定義を使い、名前付き役を指定できないホストでは対応するモデル・推論・役割指示を明示します。

Verifierは要件から独立した実行検証を行い、確認済み条件と未検証条件を分けます。Sentinelは差分と関連箇所の整合性、暗黙の前提、回帰、検証漏れを調べ、発生条件・影響・根拠をRootへ返します。実行結果とレビューのどちらが不足しているかで担当を選び、異なる証拠が必要な場合だけ両方を使います。重大なリスクはInquisitorへ直接渡します。

修正の割り当て・統合・最終受け入れはRootが担当します。役割の権限、子からの再委譲禁止、上限8・通常2〜4体・同時書き込み最大3体、全役のコンテキスト100万・自動コンパクション90万は維持します。修正後は影響範囲を再確認し、要件・依存先・重要な証拠の変更でも該当する結論を見直します。

## 既存の親環境への反映

配布元のブランチを更新しても、導入済みの親や実行中のタスクには反映されません。配布元で検証後、実際の非Git親を指定して更新内容を確認します。

```bash
make validate
make install-dry-run
./scripts/sync.sh --target "/absolute/path/to/guild-root" --dry-run
./scripts/sync.sh --target "/absolute/path/to/guild-root"
```

未変更の管理対象は更新され、独自ファイルは保持されます。配布元と導入先の両方で同じ管理ファイルを変更した場合は、書き込み前に衝突として停止します。差分を確認して独自編集を手動で整合させてください。衝突を避けるために独自編集を破棄する必要はありません。

親の`.codex/config.toml`がユーザー管理の場合、インストーラーは上書きせず`next_steps`に必要設定を返します。既存のTOMLテーブル内へ対応するキーを反映し、同名テーブルを重複追加しないでください。特に次を確認します。

- Rootの`model = "gpt-6-astra"`と通常`xhigh`推奨の案内。配布設定にRootの`model_reasoning_effort`は追加せず、利用者の推論レベル選択を優先する。
- `model_context_window = 1000000`と`model_auto_compact_token_limit = 900000`。
- `[agents]`の`enabled = true`、`max_concurrent_threads_per_session = 8`、`default_subagent_model = "gpt-6-luna"`、`default_subagent_reasoning_effort = "max"`。
- `.codex/agents/`の5役、特に`sentinel.toml`の`gpt-6-sol/xhigh`、`inquisitor.toml`の`gpt-6-astra/max`と、各役の権限・追加エージェント起動禁止。
- 親のAGENTS管理ブロック、`design-review`と`verify-change`の指示。親の`AGENTS.override.md`、子設定、ユーザー/セッション上書きとの競合。

更新後は親を開いた新しいタスクで確認します。名前付き定義は明示した起動値や既定値より優先されるため、導入先の名前付き定義が上の役割表のモデル・推論レベルと一致することを確認してください。[公式の設定優先順位](https://learn.chatgpt.com/docs/agent-configuration/subagents)

失敗時の復元と衝突時の保持方針は[既存の移行ガイド](migration-v3.md#失敗時の復元)と共通です。

## 検証と評価の範囲

オフライン検証は配布設定、旧5役からの更新、ユーザー設定と子リポジトリの保持、再同期の冪等性、衝突時の無書き込み、probeの不一致検知を扱います。名前付き役の実起動を観測したことや、品質・速度・使用量の改善を意味しません。実起動の確認方法と過去の観測は[親配置の検証記録](parent-layout.md)を参照してください。

評価器ではInquisitorがAstra/xhighの既存条件`mixed-luna-v1`を保持し、Astra/maxの新条件`mixed-luna-v2`を追加します。Rootは両条件で利用者が推論レベルを選べます。推奨の`xhigh`を記録するときは`provenance.root_override = true`とし、評価上の従来の`high`基準は変更しません。旧GPT-5.6 Lunaのbaselineとfixtureも保持し、条件IDとRootのモデル・推論レベルごとに集計します。合成データは集計の検証専用です。詳細は[評価手順](model-selection-evaluation.md)を参照してください。
