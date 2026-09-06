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

2026-09-06、macOSのCodex Desktop付属CLI **0.153.3**のapp-serverで、maintainer専用の[`scripts/check_codex_parent.py`](../scripts/check_codex_parent.py)を実行しました。scriptは現行`template`を使い捨ての非Git親へinstallし、その下に一時子Git rootを作ります。子には親と衝突する`model`と`agents.enabled`、子専用Skillを置きます。検証専用の一時`CODEX_HOME`にはtrust設定だけを書き、既存authがある場合も`auth.json`をsymlinkで参照します。実利用者のconfigや対象repositoryは変更せず、認証情報とmodelの回答本文を結果JSONへ保存しません。結果は指定pathまたはOSの一時directoryへ出力し、通常実行はmodel callを行いません。

| 確認 | 結果 |
| --- | --- |
| trusted親の`config/read` | `gpt-6-astra`、context 1,000,000、effort未固定、agents enabled/max2、`multi_agent`を取得 |
| 親の`skills/list` | 親projectのcore五つをenabled・parse errorなしで検出。cwd別に判定し、CODEX_HOMEのsystem/user Skillはproject Skillの判定から除外 |
| 親のephemeral `thread/start` | Astra、effort未固定、`instructionSources`に親fixtureの`AGENTS.md`、`approvalPolicy=never`、sandbox `readOnly`を確認 |
| 子Git rootの`config/read` | 子に置いた`child-collision-model`と`agents.enabled=false`を取得。親のmodelは子project layerに現れなかった |
| 子Git rootの`skills/list` | 子専用Skillを検出。子cwdの結果に親のcore Skillがなく、親cwdの結果にも子Skillがないことを確認 |
| named agent定義 | 配布TOMLの`name/model/model_reasoning_effort/sandbox_mode/instructions`を構造検証し、`adventurer=Luna/max/workspace-write`、`inquisitor=Astra/xhigh/read-only`を確認 |
| native named spawn（`--live`） | 通信を許可した確認でも、`low`で`adventurer`を要求した1ターン目が45秒の上限に達して停止。`item/completed`の`collabAgentToolCall`、child thread metadataは0件。`xhigh`/`inquisitor`へは進めていないため、実spawn・root effort切替後の子指定維持・child effective permissionは**unknown** |

`--live`を付けない通常実行ではmodelへ送信しません。`--live`は`low→adventurer`、`xhigh→inquisitor`の最大2ターンを要求し、各turnのnative `collabAgentToolCall`と`thread/read` metadataが揃ったときだけ`observed`にします。spawnなしを成功扱いせず、retryable error notificationまたはtimeoutは`unknown`（構造化code/typeがあればsanitized evidence付き）、構造化不一致は`failed`としてJSONに残します。今回の通信制限下での初回確認はretryable error notificationで停止し、通信許可後の確認は上記timeoutで停止しました。原因をモデルや設定の不具合と断定せず、追加のlive retryは行っていません。導入先でnamed agentを実行しmetadataを取得するまで、実行時discovery、root effort切替後の子model/effort、child effective permissionは確認済みとはしません。custom agentのread-only宣言だけをOS上の権限保証にはしません。

このmaintainer用実機検証にはPython 3.11以降とホストのCodex CLIが必要です。通常のinstall/syncはDockerだけを使い、このscriptを配布先へ導入しません。再確認時は`python3 scripts/check_codex_parent.py --output /tmp/codex-parent-smoke.json`を実行し、CLIがPATHにない場合は`--codex /absolute/path/to/codex`を指定します。実spawnを試す場合だけ`--live --live-timeout 45`を追加してください。出力の`native_spawn`が`unknown`または`failed`なら、named agentが使えたとは判断しません。

## 導入・移行の検証

`make validate`はDocker内で一時親/子fixtureを作り、dry-run、install、sync、旧配布hashでの移行、衝突、失敗時の復元、明示的な子v3整理を試験します。子の全file（.gitを含む）、index、config、modeを比較します。子にはstage済み・未stage・untracked変更、独自設定、第三者Skillを置き、通常導入の前後で同一であることを検証します。

`bash scripts/test-docker-install.sh`は実Dockerで同じ親コマンドの導入・更新・旧親移行、linked worktreeを含む子の不変性、子整理のread-only Git mountを確認します。移行手順と復元の範囲は[移行ガイド](migration-v3.md)を参照してください。
