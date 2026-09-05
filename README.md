<img src="docs/assets/agent-guild-orchestra-social-preview.png" alt="Agent Guild Orchestra">

# Agent Guild Orchestra

Agent Guild Orchestra 3.0.0は、Codexのproject-local設定、二つのcustom agent、五つのSkill、安全確認用の小さなhelperをGitリポジトリへ配布するテンプレートです。常駐serviceや独自schedulerはありません。Codex自身の会話履歴、subagent、message、approvalを使います。

> [!IMPORTANT]
> このプロジェクトは独立したコミュニティプロジェクトであり、OpenAIによる公式提供、提携、支援、承認を受けたものではありません。

## 動作の概要

Rootはgpt-6-astra / highで動きます。利用者がsessionでreasoning effortを明示した場合は、その選択を優先します。Rootは小さな作業を直接完了でき、分離する価値がある実装をAdventurerへ渡します。Adventurerはgpt-5.6-luna / maxです。security、installer、Git、migration、互換性などのmaterial riskは、実装者から独立したread-onlyのInquisitor（Astra / high）が確認します。

custom agentはAdventurerとInquisitorだけです。旧版の十role、Quest / Party / Guild、rank、SQLite queue、inbox、Ledger、dashboard、Stop hook、二重settingsは3.0.0にありません。

## 前提

- Git
- Python 3.11以上
- project-local custom agentを利用できるCodex

外部Python packageとDockerは通常の導入・検証に不要です。

## 新規導入

導入先は既存の実Git working treeのrootを指定します。特別なGuild rootやrepositories/階層は不要です。

~~~bash
git clone https://github.com/nir-nmttg/agent-guild-orchestra.git
cd agent-guild-orchestra
make validate

./scripts/install.sh --target /absolute/path/to/repository --dry-run
./scripts/install.sh --target /absolute/path/to/repository --config-mode managed
~~~

インストーラーはcanonical Git rootを照合し、書き込み前に全pathと衝突を検査します。AGENTS.mdはmarkerで囲まれた管理blockだけを更新し、block外を保持します。その他の配布ファイルは.agents/orchestra/install-manifest.jsonへ導入時hashを記録します。

`--config-mode managed`は配布元の`.codex/config.toml`を導入・更新し、`--config-mode user-owned`は既存の設定をbyte-identicalで保持してmanifestへownershipを記録します。user-ownedを選んだ場合も、配布物が要求するAstra/high、agents enabled/max2、multi_agentの論理設定は利用者が確認します。installerの成功はファイル配置の成功であり、Codexへのactivationを意味しません。

## Codexでの有効化

導入後にtarget repositoryをCodexでtrustし、target rootから新しいtaskを開始します。そのfresh taskでeffective configuration、実際のRoot model/effort、named agent `adventurer` / `inquisitor`のdiscoveryを確認します。user-owned configやsessionのmodel/effort overrideは、配布defaultより優先されます。実際にeffective modelやpermissionが何だったかは、設定ファイルのparseだけでは証明できません。

## 更新

~~~bash
git pull --ff-only
make validate
./scripts/sync.sh --target /absolute/path/to/repository --dry-run
./scripts/sync.sh --target /absolute/path/to/repository
~~~

導入先と新しい配布元の両方で同じmanaged fileが変わった場合、更新は衝突として停止します。導入先だけの変更は保持されます。更新はcandidateを先に組み立て、変更対象をtransaction backupへ退避してからatomicに反映し、途中で失敗すると元へ戻します。symlinkを経由する管理pathは拒否します。

2.4以前からの更新には--major-upgradeが必要です。旧版の標準的な非Git Guild rootを廃止して配下の実repositoryへ導入する場合は、`--legacy-root /absolute/old-guild-root`も明示します。詳細は[3.0移行ガイド](docs/migration-v3.md)を参照してください。

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
./scripts/install.sh --target /absolute/path/to/repository \
  --with-skill create-skill-candidate-from-gap
~~~

選択済みpackageは次回更新でも維持されます。外す時は--without-skill NAMEを使います。

## 安全境界とhelper

.agents/orchestra/scripts/には二つのstateless helperがあります。

- snapshot_digest.py: actual Git rootとrevision / working tree / commit rangeのcanonical snapshotを発行
- git_guard.py: snapshot、scope、operation、preconditionを照合して限定されたlocal Git操作を実行し、postcondition snapshotを返す

helperはcallerの身元や権限を証明しません。sandboxとCodex approvalが実際の権限境界です。Git対象、scope、operation、pre/post snapshotを照合して、古い根拠や別repoへの取り違えを防ぎます。

通常のhandoffとcheckpointはnative task historyで足ります。明示的な再開境界が必要な場合だけ、secretやraw logを含まないsanitized checkpointを使います。

Git hooksとsigningはhelperのlocal operationではskipされます。Git LFS/content-filter repository、content filter/process設定、tracked leaf symlinkはsnapshot/Git writeのunsupported境界です。credential-like filenameはworkerの固定heuristicで読み取り対象から除外されます。

[runtime設計](docs/orchestration-runtime.md)と[security model](docs/security-model.md)に、委譲判断、独立review、Git操作、外部更新の扱いを記載しています。

## 検証

~~~bash
make validate
make install-dry-run
~~~

validatorは配布構造とCodex設定をparseし、installerのfresh install、dry-run、update、optional package、v2 archive、衝突、symlink、transaction restoreを一時Git repoで実行します。snapshot/Git helperのpositive / negative testとmodel benchmark accountingのsynthetic smokeも実行します。

モデル比較のoffline fixtureはrecord schemaと集計だけを検証します。品質、token削減、費用削減の証拠ではありません。実modelのpilot / holdout手順は[モデル選択評価](docs/model-selection-evaluation.md)にあります。このrelease作業では高額なlive benchmarkを実行していません。

## 制約

- 設定形式、model提供状況、Codexのcustom agent機能はCodex側の変更を受けます。
- templateの規則はOS、Git hosting、Codex sandbox、approvalを置き換えません。
- publish、push、PR作成、deployなどの外部更新は、内容とtargetを確認してから実行します。
- repository内の文書、issue、web内容、tool出力は上位指示を変更するauthorityではありません。

コントリビューションは[CONTRIBUTING.md](CONTRIBUTING.md)、脆弱性報告は[SECURITY.md](SECURITY.md)、利用条件は[MIT License](LICENSE)を参照してください。
