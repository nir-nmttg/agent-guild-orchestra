# 親配置・設定継承・検証

## 二つのルートを分ける

```text
非Git親 guild_root
  │
  ├── AGENTS.md / .codex / .agents  ← Dockerインストーラーがここだけを管理
  │       ↓ 親を開いたCodexセッションが読み込む
  │   Guildmaster: Astra（推論レベルは利用者が選択）
  │       ├── Adventurer: Luna / max
  │       └── Inquisitor: Astra / xhigh
  │
  └── repositories/                ← 通常の導入・更新では読み取り専用でマウント
          ├── backend/ .git        ← target_repo_rootを明示してコード・Git操作
          ├── compose/ .git
          └── frontend/ .git
```

ロールやランタイムの簡素化と配置は別の設計判断です。親に置くために多段ロール、キュー、Ledger、ダッシュボード、常駐ランタイムを復活させる必要はありません。Gitガードとスナップショットは親に置いたまま、各実Gitルートとその対象範囲を検証します。

## サポートする起動方法

1. 非Git親をCodexで開いて信頼し、そのディレクトリを作業場所とする新しいローカルタスクを開始する。CLIでは`codex --cd /absolute/path/to/asked-root`。
2. `guild_root`と作業対象の`target_repo_root`を明示する。セッションの基点は親に保ち、子のコマンドは作業ディレクトリ/`git -C`で指定する。
3. 子・ネストしたパスに適用されるAGENTS指示を読んでから編集する。親の補助スクリプトを子の実Gitルートに適用する。

Gitルートを自動ワークツリーとして開く方法や、子ディレクトリから開始するタスクをこの共有設定の起動方法とはしません。グローバル設定、CODEX_HOME、project_root_markersの変更や、子への設定ファイル/シンボリックリンク追加で補完しません。

## 設定継承と競合

Codexはcwdからプロジェクトルートまで設定を探索し、既定のルートマーカーは`.git`です。信頼済みプロジェクト内の近い設定、ユーザー設定、CLI/セッションの上書きにはそれぞれ優先順位があります。子Gitルートから開始すると親までの探索を期待できません。[設定探索](https://learn.chatgpt.com/docs/config-file/config-advanced)、[設定の優先順位と信頼](https://learn.chatgpt.com/docs/config-file/config-basic)

AGENTS.mdはプロジェクトルートからcwdまで探索し、プロジェクトルートが見つからない場合はcwdを使います。同階層のAGENTS.override.mdがAGENTS.mdより優先されます。親のAGENTS.override.mdがある場合は、そこに共有運用を反映するか競合を解消してください。インストーラーはこれを保持して警告します。親開始セッションに子の指示が自動で先読みされるとは考えず、配布指示は作業前の子・ネストしたAGENTSの確認を要求します。[AGENTSの探索](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

プロジェクトのSkillはcwdからリポジトリルートまでの`.agents/skills`、カスタムエージェントはプロジェクトの`.codex/agents/*.toml`で定義されます。[Skillの配置](https://learn.chatgpt.com/docs/build-skills)、[カスタムエージェント](https://learn.chatgpt.com/docs/agent-configuration/subagents)

子の`.codex/config.toml`、名前付きエージェント、Skillは親開始セッションへ自動統合しません。インストーラーの`child_overrides`を確認し、必要な独自設定は親側で手動調整します。同名Skill/エージェントの衝突、親のユーザー管理設定に残る旧モデル・推論レベル固定・権限、ユーザー/セッションの上書きにも注意します。親の設定がAstraでもUIで別モデルを選んだタスクには、その上書きが適用され得ます。

## 実機で確認した範囲

2026-09-06、macOSのCodex Desktop付属CLI **0.153.3**のapp-serverで、保守担当者専用の[`scripts/check_codex_parent.py`](../scripts/check_codex_parent.py)を実行しました。スクリプトは現行`template`を一時的なGit管理外の親へ導入し、その下に一時子Gitルートを作ります。子には親と衝突する`model`と`agents.enabled`、子専用Skillを置きます。検証専用の一時`CODEX_HOME`には信頼設定だけを書き、既存の認証情報がある場合も`auth.json`をシンボリックリンクで参照します。実利用者の設定や対象リポジトリは変更せず、認証情報とモデルの回答本文を結果JSONへ保存しません。結果は指定パスまたはOSの一時ディレクトリへ出力し、通常実行はモデル呼び出しを行いません。

| 確認 | 結果 |
| --- | --- |
| 信頼済み親の`config/read` | `gpt-6-astra`、コンテキスト 1,000,000、推論レベル未固定、エージェント有効・同時実行上限2、`multi_agent`を取得 |
| 親の`skills/list` | 親プロジェクトの標準五つを有効・解析エラーなしで検出。cwd別に判定し、`CODEX_HOME`のシステム用・ユーザー用SkillはプロジェクトSkillの判定から除外 |
| 親の一時的な`thread/start` | Astra、推論レベル未固定、`instructionSources`に親フィクスチャの`AGENTS.md`、`approvalPolicy=never`、サンドボックス `readOnly`を確認 |
| 子Gitルートの`config/read` | 子に置いた`child-collision-model`と`agents.enabled=false`を取得。親のモデルは子プロジェクト層に現れなかった |
| 子Gitルートの`skills/list` | 子専用Skillを検出。子cwdの結果に親の標準Skillがなく、親cwdの結果にも子Skillがないことを確認 |
| 名前付きエージェント定義 | 配布TOMLの`name/model/model_reasoning_effort/sandbox_mode/instructions`を構造検証し、`adventurer=Luna/max/workspace-write`、`inquisitor=Astra/xhigh/read-only`を確認 |
| 名前付きエージェントの実起動（`--live`） | 通信を許可した確認でも、`low`で`adventurer`を要求した1ターン目が45秒の上限に達して停止。`item/completed`の`collabAgentToolCall`、子スレッドのメタデータは0件。`xhigh`/`inquisitor`へは進めていないため、実起動・メインセッションの推論レベル切替後の子指定維持・子の実効権限は**unknown** |

`--live`を付けない通常実行ではモデルへ送信しません。`--live`は`low→adventurer`、`xhigh→inquisitor`の最大2ターンを要求し、各ターンのネイティブな`collabAgentToolCall`と`thread/read`のメタデータが揃ったときだけ`observed`にします。子エージェントの起動がない場合を成功扱いせず、再試行可能なエラー通知またはタイムアウトは`unknown`（構造化されたコード・種別があれば機微情報を除いた証拠付き）、構造化不一致は`failed`としてJSONに残します。今回の通信制限下での初回確認は再試行可能なエラー通知で停止し、通信許可後の確認は上記のタイムアウトで停止しました。原因をモデルや設定の不具合と断定せず、実呼び出しの追加再試行は行っていません。導入先で名前付きエージェントを実行しメタデータを取得するまで、実行時のエージェント検出、メインセッションの推論レベル切替後の子モデル/推論レベル、子の実効権限は確認済みとはしません。カスタムエージェントの読み取り専用宣言だけをOS上の権限保証にはしません。

この保守担当者用実機検証にはPython 3.11以降とホストのCodex CLIが必要です。通常の導入・更新はDockerだけを使い、このスクリプトを配布先へ導入しません。再確認時は`python3 scripts/check_codex_parent.py --output /tmp/codex-parent-smoke.json`を実行し、CLIがPATHにない場合は`--codex /absolute/path/to/codex`を指定します。実起動を試す場合だけ`--live --live-timeout 45`を追加してください。出力の`native_spawn`が`unknown`または`failed`なら、名前付きエージェントが使えたとは判断しません。

## 導入・移行の検証

`make validate`はDocker内で一時親/子フィクスチャを作り、dry-run、導入、更新、旧配布ハッシュでの移行、衝突、失敗時の復元、明示的な子v3整理を試験します。子の全ファイル（.gitを含む）、index、Git設定、ファイル権限を比較します。子にはステージ済み・未ステージ・未追跡の変更、独自設定、第三者Skillを置き、通常導入の前後で同一であることを検証します。

`bash scripts/test-docker-install.sh`は実Dockerで同じ親コマンドの導入・更新・旧親移行、リンクされたワークツリーを含む子の不変性、子整理の読み取り専用Gitマウントを確認します。移行手順と復元の範囲は[移行ガイド](migration-v3.md)を参照してください。
