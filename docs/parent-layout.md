# 親配置・設定継承・検証

## 二つのrootを分ける

```text
非Git親 guild_root
  │
  ├── AGENTS.md / .codex / .agents  ← Docker installerがここだけを管理
  │       ↓ 親を開いたCodex sessionが読み込む
  │   Guildmaster: Astra（effortは利用者選択）
  │       ├── Adventurer: Luna / max
  │       └── Inquisitor: Astra / xhigh
  │
  └── repositories/                ← 通常install/syncではread-only mount
          ├── backend/ .git        ← target_repo_rootを明示してコード・Git操作
          ├── compose/ .git
          └── frontend/ .git
```

ロールやruntimeの簡素化と配置は別の設計判断です。親に置くために多段ロール、queue、Ledger、dashboard、常駐runtimeを復活させる必要はありません。Git guardとsnapshotは親に置いたまま、各実Git rootとそのscopeを検証します。

## サポートする起動方法

1. 非Git親をCodexで開いてtrustし、そのdirectoryを作業場所とする新しいlocal taskを開始する。CLIでは`codex --cd /absolute/path/to/asked-root`。
2. `guild_root`と作業対象の`target_repo_root`を明示する。sessionの基点は親に保ち、子のcommandはworkdir/`git -C`で指定する。
3. 子・nested pathに適用されるAGENTS指示を読んでから編集する。親のhelperを子の実Git rootに適用する。

Git rootを自動worktreeとして開く方法や、子directoryから開始するtaskをこの共有設定の起動方法とはしません。global config、CODEX_HOME、project_root_markersの変更や、子への設定file/symlink追加で補完しません。

## 設定継承と競合

Codexはcwdからproject rootまで設定を探索し、既定のroot markerは`.git`です。trusted project内の近い設定、user設定、CLI/session overrideにはそれぞれ優先順位があります。子Git rootから開始すると親までの探索を期待できません。[設定探索](https://learn.chatgpt.com/docs/config-file/config-advanced)、[設定の優先順位とtrust](https://learn.chatgpt.com/docs/config-file/config-basic)

AGENTS.mdはproject rootからcwdまで探索し、project rootが見つからない場合はcwdを使います。同階層のAGENTS.override.mdがAGENTS.mdより優先されます。親のAGENTS.override.mdがある場合は、そこに共有運用を反映するか競合を解消してください。installerはこれを保持して警告します。親開始sessionに子の指示が自動で先読みされるとは考えず、配布指示は作業前の子・nested AGENTS確認を要求します。[AGENTSの探索](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

project Skillはcwdからrepository rootまでの`.agents/skills`、custom agentはprojectの`.codex/agents/*.toml`で定義されます。[Skillの配置](https://learn.chatgpt.com/docs/build-skills)、[custom agent](https://learn.chatgpt.com/docs/agent-configuration/subagents)

子の`.codex/config.toml`、named agent、Skillは親開始sessionへ自動mergeしません。installerの`child_overrides`を確認し、必要な独自設定は親側で手動調整します。同名Skill/agentの衝突、親のuser-owned configに残る旧モデル・effort固定・permission、user/session overrideにも注意します。親のconfigがAstraでもUIで別modelを選んだtaskには、そのoverrideが適用され得ます。

## 実機で確認した範囲

2026-09-05、macOSのCodex Desktop付属CLI **0.153.3**のapp-serverで確認しました。disposableな非Git親と子Git repoを用意し、検証専用の一時user homeにtrust設定を置きました。実利用者のconfig、credential、対象repositoryは変更していません。これは試験の隔離であり、利用手順でCODEX_HOMEを変更する設計ではありません。

| 確認 | 結果 |
| --- | --- |
| trusted親の`config/read` | `gpt-6-astra`、context 1,000,000、effort未固定、agents enabled/max2、multi_agent、experimental context managementを取得 |
| 親の`skills/list` | 親のcore五つをenabledで検出、Skill parse errorなし |
| 親のephemeral `thread/start` | Astra、effort未固定、`instructionSources`に親のAGENTS.mdを確認 |
| 子Git rootの`config/read` | 子に置いた試験用modelを取得し、親の設定layerは含まれなかった |
| 子Git rootの`skills/list` | 親のcore Skillを検出しなかった |
| 未trustの親 | project configが無効化された。SkillやAGENTSの読み込みだけをactivation証拠にしてはいけない |
| named agent | 配布TOMLのname/model/effort/instructionsと構造検証は成功。実呼び出しによるdiscovery、選択model/effort、live permissionの確認は未実施 |

modelへのlive turnは送っていません。Desktop UI固有のproject/worktree選択、ユーザー既存設定との合成、named agentの実行時の有効権限はこの試験だけでは確認できません。導入先で親から新規taskを作り、実際のmodel/effortとnamed agent利用を確認してください。custom agentのread-only宣言だけをOS上の権限保証にはしません。

## 導入・移行の検証

`make validate`はDocker内で一時親/子fixtureを作り、dry-run、install、sync、旧配布hashでの移行、衝突、失敗時の復元、明示的な子v3整理を試験します。子の全file（.gitを含む）、index、config、modeを比較します。子にはstage済み・未stage・untracked変更、独自設定、第三者Skillを置き、通常導入の前後で同一であることを検証します。

`bash scripts/test-docker-install.sh`は実Dockerで同じ親コマンドの導入・更新・旧親移行、linked worktreeを含む子の不変性、子整理のread-only Git mountを確認します。移行手順と復元の範囲は[移行ガイド](migration-v3.md)を参照してください。
