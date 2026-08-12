<img src="docs/assets/agent-guild-orchestra-social-preview.png" alt="Agent Guild Orchestraのソーシャルプレビュー">


---


# Agent Guild Orchestra


Codexを、成果品質、安全な権限境界、検証可能性を優先して動かすためのGuild runtimeテンプレートです。実作業のリポジトリとオーケストレーション用の契約・状態を分離し、作業の大きさとリスクに応じた委譲、検証、監査を支援します。


現在のバージョンは`2.2.0`です。


> [!IMPORTANT]
> このプロジェクトは独立したコミュニティプロジェクトであり、OpenAIによる公式提供、提携、支援、承認を受けたものではありません。Codex、GPTおよびOpenAIはOpenAIの商標または登録商標です。本プロジェクトはOpenAIのロゴを使用しません。


## まず知っておくこと


導入先は、実作業リポジトリそのものではなく、それらをまとめる専用のGuild rootです。


```text
<guild-root>/
├── AGENTS.md
├── .agents/
├── .codex/
├── .orchestra/
└── repositories/
    ├── app-a/
    └── app-b/
```


各作業の対象となる`target_repo_root`は、`repositories/`直下にある個別リポジトリのGit rootへ固定します。インストーラーは`repositories/`配下の実作業リポジトリを移動・削除しません。


## 前提条件


- Git
- Docker EngineまたはDocker Desktop（`docker build`と`docker run`を実行できること）
- Codexのproject-local設定とcustom agentを利用できる環境
- Docker imageの初回build時に、base imageとPython依存関係を取得できるネットワーク


通常の検証と導入でホストへPythonパッケージを直接インストールする必要はありません。`make validate`と`./scripts/docker_python.sh`は、requirementsを含むDocker image内のPythonで実行されます。hostで直接`python3`を使うのは任意の運用であり、Python 3.10以上かつ`requirements.txt`の依存関係（Python 3.10では`tomli`を含む）を満たす場合だけにしてください。


## 初回導入


### 1. cloneして配布物を検証する


```bash
git clone https://github.com/nir-nmttg/agent-guild-orchestra.git
cd agent-guild-orchestra
make validate
```


`make validate`は、安全境界、role・model設定、queue・snapshot契約、最終成果のhard gate、日本語化方針など、リポジトリが提供する一連のvalidatorを実行します。


### 2. 実際の導入先に対してdry-runする


```bash
./scripts/install.sh \
  --target /path/to/guild-root \
  --mode copy \
  --dry-run
```


出力された作成・更新対象を確認してください。導入先には、子リポジトリや`repositories/`自体ではなく、その親となるGuild rootを指定します。


### 3. バックアップ付きで導入する


```bash
./scripts/install.sh \
  --target /path/to/guild-root \
  --mode copy \
  --backup
```


既存の管理対象がある場合、変更前の状態は`<guild-root>/.agent-guild-orchestra-backups/<timestamp>/`へコピーされます。新規の空ディレクトリへ導入する場合は、バックアップ対象がないためbackupは作成されません。


導入後、実作業リポジトリを`<guild-root>/repositories/<repo>`へ配置します。


## 通常の更新


配布元リポジトリを更新し、`sync.sh`で既存環境へ反映します。`sync.sh`は更新前のバックアップを自動で有効にします。


```bash
cd /path/to/agent-guild-orchestra
git pull --ff-only
make validate
./scripts/sync.sh --target /path/to/guild-root --dry-run
./scripts/sync.sh --target /path/to/guild-root
```


通常更新では、既存の`.orchestra/queue/`、Ledger、dashboardを保持しながら静的な配布物を更新します。互換性のない古いruntime schemaが見つかった場合、インストーラーはfail closedで停止し、状態の初期化方法を案内します。
