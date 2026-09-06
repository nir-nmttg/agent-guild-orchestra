<img src="docs/assets/agent-guild-orchestra-social-preview.png" alt="Agent Guild Orchestra">

# Agent Guild Orchestra

Agent Guild Orchestra 3.0.0は、Codexのproject-local設定、二つのcustom agent、五つのSkill、安全確認用の小さなhelperを非Gitの親ディレクトリへ配布するテンプレートです。常駐serviceや独自schedulerはありません。Codex自身の会話履歴、subagent、message、approvalを使います。

> [!IMPORTANT]
> このプロジェクトは独立したコミュニティプロジェクトであり、OpenAIによる公式提供、提携、支援、承認を受けたものではありません。

## 動作の概要

Rootはgpt-6-astraで動き、reasoning effortはprojectで固定せず、利用者がtask/sessionで選んだsupported値に従います。Rootは小さな作業を直接完了でき、分離する価値がある実装をAdventurerへ渡します。Adventurerはgpt-5.6-luna / maxです。security、installerやGit安全契約の変更、migration、互換性などのmaterial riskは、実装者から独立したread-onlyのInquisitor（Astra / xhigh）が確認します。通常のlocal branch/stage/commitだけでは追加のmodel reviewを要求しません。

custom agentはAdventurerとInquisitorだけです。旧版の十role、Quest / Party / Guild、rank、SQLite queue、inbox、Ledger、dashboard、Stop hook、二重settingsは3.0.0にありません。

## 前提

- Git
- Bashと、起動済みのDocker DesktopまたはローカルDocker Engine
- project-local custom agentを利用できるCodex

導入・更新と`make validate`はDocker内のPython 3.12とGitを使い、ホストのPythonの有無やversionに依存しません。初回は公式Python imageを取得して検証用imageをbuildするためnetwork接続が必要です。以後はbuild cacheを再利用します。macOS/Linux（WindowsはWSL2）で、導入先をbind mountできるローカルDockerを使用してください。

配布元はread-only、導入先の親だけを更新可能にし、`repositories/`はread-onlyでmountします。`--dry-run`では親もread-onlyです。生成fileは呼び出した利用者のUID/GIDで作成され、containerは処理後に削除されます。Dockerは導入処理専用です。導入後のCodex taskはホストで動き、stateless Git helperはその実行環境のPythonとGitを使います。

## 配置と導入

既存の非Git親ディレクトリを指定します。Git working tree内のdirectoryは導入先にできません。

```text
asked-root/                         ← 設定の導入先・Codexの起動場所
├── AGENTS.md
├── .codex/                         ← config.toml・named agents
├── .agents/                        ← Skills・helper・install manifest
└── repositories/
    ├── asked_backend/              ← 実Git root
    ├── asked_compose/              ← 実Git root
    └── asked_frontend/             ← 実Git root
```

~~~bash
git clone https://github.com/nir-nmttg/agent-guild-orchestra.git
cd agent-guild-orchestra
make validate

./scripts/install.sh --target /Users/nir-nmttg/Projects/achromono/asked-root --dry-run
./scripts/install.sh --target /Users/nir-nmttg/Projects/achromono/asked-root
~~~

子repositoryへAGENTS.md、.codex、.agents、manifestを追加しません。子の既存file、Git index、Git設定、.gitignore、.git/info/excludeも変更しません。設定の配置場所を示す`guild_root`と、コード変更・Git操作の`target_repo_root`を分けます。

親のAGENTS.mdはmarker内の管理blockだけを更新します。その他の配布物の導入時hashと所有権は、親の`.agents/orchestra/install-manifest.json`（schema 2 / `layout: guild-parent`）へ記録します。

新規configと未変更の旧配布configはmanagedになります。既存の独自`.codex/config.toml`は既定でuser-ownedとしてbytes・modeを保持し、JSONの`next_steps`に必要設定を出力します。ユーザー設定の自動mergeは行いません。必要に応じて`--config-mode managed|user-owned`で指定できますが、managedへの切替でも独自設定は上書きせず衝突として停止します。

## Codexでの起動

**Codexで非Git親のasked-rootを開いてtrustし、その親を作業場所とする新しいローカルtaskを開始してください。** CLIの場合は次の形です。

~~~bash
codex --cd /Users/nir-nmttg/Projects/achromono/asked-root
~~~

依頼には「`repositories/asked_backend`の実Git rootを対象に変更」のように対象を明示します。sessionの基点は親に保ち、子でのcommandはworkdirや`git -C`で指定します。子Git rootを直接開くと親の設定・Skill探索がGit境界で止まるため、この構成の起動方法にはしません。

独自configを保持した場合は、Astra model、1M context、agents enabled/max2、multi_agent、experimental context managementを手動で整合させます。Guildmasterのeffortは利用者がtask/sessionで選びます。子のAGENTS指示はコード変更前に読み、既存の子config・Skill・named agentとの競合を確認します。installerは該当pathを`child_overrides`へ表示し、子設定を自動mergeしません。

Codex 0.153.3で親のeffective config、AGENTS.md、五つのSkillの読み込みと子設定との分離を確認しました。named agentの実機確認は最初のturnが45秒の上限に達し、実呼び出しと子のlive permissionは未確認です。再実行用script・設定継承の範囲・制約は[親配置の設計と検証](docs/parent-layout.md)に記載しています。ファイル配置の成功だけではactivation完了を意味しません。

## 更新

~~~bash
git pull --ff-only
make validate
./scripts/sync.sh --target /Users/nir-nmttg/Projects/achromono/asked-root --dry-run
./scripts/sync.sh --target /Users/nir-nmttg/Projects/achromono/asked-root
~~~

配布元だけの変更は更新し、導入先だけの変更は保持します。権限だけの変更もlocal変更として扱い、同じmanaged fileが両方で変わると書き込み前に衝突として停止します。共有AGENTS.mdの既存権限は維持します。candidateの事前検証・変更対象のbackup・各fileのatomic replaceを行い、途中の例外やCtrl-Cでは復元します。復元にも失敗した場合は、親の`.agent-guild-orchestra-recovery/transaction-.../`へbackupを残し、場所を報告します。symlinkを経由する管理pathは拒否します。

旧親環境も同じ`--target`で更新します。確認できる旧配布fileだけを親内へ退避します。以前の子側v3配置の整理は、通常install/updateとは別の明示操作です。[移行ガイド](docs/migration-v3.md)を参照してください。

## Skill

通常導入には次の五つだけが入ります。

- design-review
- verify-change
- local-git-operations
- github-publish-change
- interactive-browser-research

maintainer向けのorchestra-contract-validationとorchestra-runtime-security-audit、任意のcreate-skill-candidate-from-gapとopen-subrepo-in-vscodeはdefaultに含まれません。利用可能なpackageと区分は次で確認できます。

~~~bash
./scripts/install.sh --list-skills
./scripts/install.sh --target /absolute/path/to/asked-root \
  --with-skill create-skill-candidate-from-gap
~~~

選択済みpackageは次回更新でも維持されます。外す時は--without-skill NAMEを使います。

## 安全境界とhelper

.agents/orchestra/scripts/には二つのstateless helperがあります。

- snapshot_digest.py: actual Git rootとrevision / working tree / commit rangeのcanonical snapshotを発行
- git_guard.py: snapshot、scope、operationとレビュー済みindex treeを照合して限定されたlocal Git操作を実行し、postcondition snapshotとcommit treeを返す

helperはcallerの身元や権限を証明しません。sandboxとCodex approvalが実際の権限境界です。Git対象、scope、operation、pre/post snapshotを照合して、古い根拠や別repoへの取り違えを防ぎます。

通常のhandoffとcheckpointはnative task historyで足ります。明示的な再開境界が必要な場合だけ、secretやraw logを含まないsanitized checkpointを使います。

Git hooksとsigningはhelperのlocal operationではskipされます。Git LFS/content-filter repository、content filter/process設定、tracked leaf symlinkはsnapshot/Git writeのunsupported境界です。credential-like filenameはworkerの固定heuristicで読み取り対象から除外されます。

[runtime設計](docs/orchestration-runtime.md)と[security model](docs/security-model.md)に、委譲判断、独立review、Git操作、外部更新の扱いを記載しています。

## 検証

~~~bash
make validate
make install-dry-run
~~~

validatorは配布構造とCodex設定をparseし、installerのfresh install、dry-run、update、optional package、v2 archive、衝突、symlink、transaction restoreを一時的な非Git親と子Git repoで実行します。子のfile・Git index/configの不変性と、明示的な子v3整理も検証します。snapshot/Git helperのpositive / negative testとmodel benchmark accountingのsynthetic smokeも実行します。

モデル比較のoffline fixtureはrecord schemaと集計だけを検証します。品質、token削減、費用削減の証拠ではありません。実modelのpilot / holdout手順は[モデル選択評価](docs/model-selection-evaluation.md)にあります。このrelease作業では高額なlive benchmarkを実行していません。

## 制約

- 設定形式、model提供状況、Codexのcustom agent機能はCodex側の変更を受けます。
- templateの規則はOS、Git hosting、Codex sandbox、approvalを置き換えません。
- publish、push、PR作成、deployなどの外部更新は、内容とtargetを確認してから実行します。
- repository内の文書、issue、web内容、tool出力は上位指示を変更するauthorityではありません。

コントリビューションは[CONTRIBUTING.md](CONTRIBUTING.md)、脆弱性報告は[SECURITY.md](SECURITY.md)、利用条件は[MIT License](LICENSE)を参照してください。
