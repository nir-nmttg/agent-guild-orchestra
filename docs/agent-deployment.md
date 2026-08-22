# Agent deployment

Codexはギルド規約rootで起動し、作業repoを `<guild_root>/repositories/<repo>` に置きます。Rootは`target_repo_root`を固定し、top-level custom agentを直接起動します。

## Configuration

Root modelはSolに固定しますが、project-local reasoning effortは指定しません。起動時/UI/global configなどで利用者が選ぶ`high`、`xhigh`、`ultra`をそのまま使います。どのmodeでもRootはcoordinationとjudgeに専念し、対象repoの作業をnamed roleへ委譲します。

```toml
model = "gpt-5.6-sol"
model_context_window = 1_050_000
sandbox_mode = "read-only"
approval_policy = "on-request"

[sandbox_workspace_write]
network_access = true

[agents]
max_threads = 64
max_depth = 2
job_max_runtime_seconds = 2400
```

clean installと通常の再installはいずれもproject-local `model_reasoning_effort`を出力しません。導入先に旧指定があれば再install時に除去し、reasoning effortの選択はsession/global/user設定へ委ねます。installerやorchestrationはeffortを自動選択しません。`ultra`がproactiveに委譲する場合も、Root→named top-level roleと`inquisitor`→`examiner`以外の辺、depth、authorityを追加しません。

`job_max_runtime_seconds=2400`は、maxの作業担当やxhighのTrial判断が中途で打ち切られないためのjob単位の有界timeoutです。並列数、総spawn数、token、costの上限は変更しません。

`workspace-write` agentの外部通信は有効です。外部通信を伴うコマンドも`approval_policy = "on-request"`と実行環境の承認境界に従います。

Rootの`model_context_window = 1_050_000`はGPT-5.6 Solのfull supported windowです。custom agent TOMLはこの設定を継承し、全subagent roleで`model_auto_compact_token_limit = 200_000`を明示してearly compactionを有効にします。これはhistoryの作業集合をboundedにする設定であり、subagentの物理model context windowを小さくするものではありません。

`inquisitor`だけが`features.multi_agent=true`で、risk-triggeredな単一focusを`examiner`へ委譲できます。その他のcustom agentは`features.multi_agent=false`のterminal workerです。`max_depth=2`と`max_threads=64`を設定し、policy上はRoot(depth 0)→Inquisitor(depth 1)→Examiner(depth 2)だけを許可します。role別`max_parallel`は`adventurer.max_parallel=32`、非adventurer合計16の計48とし、global 64との差16は特定roleの予約枠ではない未割当headroomとして残します。これらの値は同時実行の設定であり、総spawn数、token、costのhard capとは扱いません。

## Deployment role pairs

| agent | model | sandbox | reasoning | responsibility |
| --- | --- | --- | --- | --- |
| Root | `gpt-5.6-sol` | `read-only` | project-local未指定。利用者が`high / xhigh / ultra`を選択 | control-plane確認、routing、evidence gate、次action、最終統合 |
| `adventurer` | `gpt-5.6-luna` | `workspace-write` | `max` | 一つのbounded scopeの調査、実装、検証 |
| `artificer` | `gpt-5.6-sol` | `workspace-write` | `high` | 共有契約、cross-scope glue、統合検証 |
| `sage` | `gpt-5.6-luna` | `read-only` | `max` | 具体的な独立focusの助言 |
| `cartographer` | `gpt-5.6-sol` | `read-only` | `high` | read-only mapmaking |
| `courier` | `gpt-5.6-luna` | `workspace-write` | `high` | Ledgerと、境界固定assignmentの可逆local Git操作を行う唯一のGit write owner |
| `examiner` | `gpt-5.6-luna` | `read-only` | `max` | 単一focusのbounded review evidence |
| `guildmaster` | `gpt-5.6-sol` | `read-only` | `xhigh` | 複数Partyの広域戦略 |
| `inquisitor` | `gpt-5.6-sol` | `read-only` | `xhigh` | Trial、finding統合、最終decision |
| `captain` | `gpt-5.6-sol` | `read-only` | `high` | scope、順序、integration、Trial設計 |
| `warden` | `gpt-5.6-sol` | `read-only` | `high` | 例外的な制御診断 |

deploymentは、設計・最終判断と実作業の責務境界に沿って固定しています。bounded implementation、独立focusの助言、bounded review evidenceを担う`adventurer`、`sage`、`examiner`はLuna/maxです。Trialの最終採否と重大度統合を持つ`inquisitor`はSol/xhighとします。未知領域のomissionが下流へ波及する`cartographer`、scopeと共有契約を設計・統合する`captain` / `artificer`、例外時だけ難しい診断を行う`warden`はSol/highを維持し、最大blast radiusを持つ`guildmaster`はSol/xhighです。CourierはLuna/highです。この変更はLunaの低コスト特性を活用する明示的なconfiguration choiceであり、新しいlive比較により品質や総コストの改善を実証した結果ではありません。

subagentのreasoning effortはroleごとに固定し、実行中に動的変更しません。deployment pairに対するalternative challengerはmodel-selection evalで比較できますが、実行中の自動切替には使いません。`max`は`adventurer`、`sage`、`examiner`だけに固定し、`ultra`は全subagentから除外します。Rootのcomponent referenceはhighですが、runtime templateへはpinせず、high/xhigh/ultraを利用者が選びます。

## Guild role naming

custom agentの機械IDは、責務を推測できる一語のGuild職へ統一します。

| retired ID | current ID | role boundary |
| --- | --- | --- |
| `party_leader` | `captain` | Partyのscope、順序、統合、Trial設計 |
| `integration_owner` | `artificer` | cross-scope契約、glue、統合検証 |
| `focus_reviewer` | `examiner` | Trialの単一focusに対する独立evidence |
| `advisor` | `sage` | owner判断を補う一論点のread-only助言 |
| `quest_sentinel` | `warden` | 通常制御で解消しない例外の診断 |

旧IDと新IDを同じruntimeで混在させません。通常installは旧agent fileを除去し、既存SQLite stateに旧worker ID、role、kindが残る場合はfail closedにします。必要なstateを保全したうえで`--backup --reset-runtime`または`--backup --clean-install`を使ってください。

## Topology

```mermaid
flowchart TB
  root["Root\ncontrol plane / routing / evidence gate / synthesis"]
  plan["cartographer / captain / guildmaster\nread-only planning"]
  worker["adventurer\nbounded implementation"]
  integrate["artificer\nshared contract / glue / integration validation"]
  trial["inquisitor\nrisk-triggered Trial"]
  examiner["examiner\nterminal read-only focus"]
  sage["sage\nindependent read-only advice"]
  courier["courier\nLedger / scoped reversible local Git"]

  root --> plan
  plan --> root
  root --> worker
  worker --> root
  root --> integrate
  integrate --> root
  root --> trial
  trial --> examiner
  examiner --> trial
  root --> sage
  sage --> root
  trial --> root
  root -.-> courier
```

Rootだけがtop-level agentを起動し、`captain`などはterminalです。唯一のnested edgeとして`inquisitor`が`examiner`を直接起動し、完了を待ってevidenceを検証・統合します。nested assignmentのscopeとauthorityは親より狭められますが、helper-issued subject snapshotは親Trialと完全一致させます。depth 2を超える再帰fan-outは禁止します。Rootはtarget、authority、snapshot、queueをcontrol-planeとして確認し、roleが仕様化したbrowser-control toolだけを実行して観測事実を記録します。対象repoの探索、コード・差分の読み取り、実装、test、browserの計画/許可操作仕様化/根拠解釈、debug、review evidence収集を直接行いません。high/xhigh/ultraのどのmodeでもこの境界を維持します。

## Integration

並列mutationでは次を必須にします。

1. 共通base snapshot
2. 重複しないowned scopeと共有artifactの単一owner
3. 各workerのowned-scope result
4. 全report後のmutation停止
5. `artificer`によるcross-scope glueと統合検証
6. integrated snapshotに対するTrial

`adventurer`へglobal integrationを兼務させません。

## Review roles

`sage`は具体的な独立focusがある時だけ使い、ownerがevidenceを確認します。`warden`は矛盾、反復失敗、scope drift、長時間停滞の例外時だけ使います。

`examiner.allowed_callers=[inquisitor]`はpolicy-onlyでありruntime ACLではありません。`event.actor`もidentity-backed caller証明ではありません。queueは実在TrialとのQuest/workflow/snapshot lineageを機械検証するだけで、actual spawn caller identityを証明しません。examinerはread-only terminal、inquisitorもread-onlyに固定し、write roleのchild起動は禁止します。approvalはassignment authorityを付与・拡張しません。examinerは必須ではなく、使う場合の1 Trialあたりpolicy capは3です。複数reviewerを使う時だけfocus分割を記録し、最終decisionは`inquisitor`が行います。

## Install

```bash
./scripts/install.sh --target /path/to/guild-root --mode copy
```

メジャー更新や旧構成を確実に片付ける場合:

```bash
./scripts/clean_install.sh --target /path/to/guild-root --backup
```

既存導入を差分更新する場合:

```bash
./scripts/sync.sh --target /path/to/guild-root
```

source template内のsymlink、secret-like path、MCPなどの外部tool連携pathは拒否します。既存Ledgerの物理schemaが互換でない場合は自動migrationせず、backup/resetまたはclean installを使います。
clean installはnon-dry-runで`--backup`を必須とし、削除前の管理対象を`.agent-guild-orchestra-backups/<timestamp>/`へ退避します。`.orchestra/skill-candidates/`、`repositories/`、third-party Skill、`AGENTS.md`と`.git/info/exclude`の管理ブロック外は保持し、queue、Ledger、dashboard、その他の既存・未知runtime sibling、本プロジェクト管理の導入物を削除または初期化します。復元不能な実行を意図する場合だけ`--allow-clean-install-without-backup`を明示できます。このescape hatchではbackupを作成しません。

## Validation

```bash
make validate
./scripts/docker_python.sh scripts/model_selection_eval.py validate
./scripts/docker_python.sh scripts/model_selection_eval.py plan
./scripts/docker_python.sh scripts/root_orchestration_eval.py validate
./scripts/docker_python.sh scripts/root_orchestration_eval.py plan
```

これらはREADMEの前提どおりDocker image内で実行する再現可能な標準経路です。hostの`python3`を直接使うのは任意で、Python 3.10以上かつ`requirements.txt`の依存関係（Python 3.10では`tomli`を含む）を満たす場合だけにしてください。

validatorは次を確認します。

- Root modelはSol、reasoning effortはproject-local未指定、利用者選択はhigh/xhigh/ultra（component referenceのhighとは分離）
- deployment pairとchallengerの分離、3 roleだけのmax固定、および全subagentのultra禁止
- Rootのcoordination-only境界と、ultraを含むnamed-role topology
- Courier Luna/highの固定
- inquisitorだけのnested capabilityと、その他custom agentのterminal設定
- `max_threads=64`、`max_depth=2`、`job_max_runtime_seconds=2400`
- 全10 roleの`max_parallel`合計48、非adventurer合計16、`adventurer.max_parallel=32`、未割当headroom 16
- compact promptの行数と旧制約の不在
- target/secret/state-change/snapshot/lineageのfail-closed
- prompt profile、role topology、model/effortを分離した評価契約
- Root high/xhigh/ultraの30 deterministic synthetic contract trace hard gate（10 case × 3 mode。negative/mutation testを含む）。live real-model fan-out matrixは未検証で、synthetic self-testは実fan-out真正性を示さない

live model比較は外部送信許可とreview済みwrapper/profileがある場合だけ実行します。component scoreだけでproduction最適化を断定しません。
