# デプロイパターン

このテンプレートは `copy` モードを基本にした、ギルド規約ルートへの薄い導入だけを前提にします。
役割 agent、Skill、Ledger 補助の配置詳細は [agent-deployment.md](agent-deployment.md) を参照してください。

## 推奨構成

- ギルド規約ルートへ `template/` を導入する
- ギルド規約ルート直下に `repositories/` を置く
- 開発対象の子リポジトリは必ず `<guild_root>/repositories/<repo>` に置く
- `target_repo_root` は Root が明示した `<guild_root>/repositories/<repo>` の Git ルートだけにする
- 対象 repo の探索、編集、検証、git 操作は `target_repo_root` に限定する
- `.agents/orchestra` と `.orchestra` は runtime contract（静的契約）/runtime state（動的状態）として読めるが、そこから target repo を再特定、変更、拡張しない
- 子リポジトリへ `.agents`、`.codex`、`agent-guild-orchestra` 管理ブロックを再導入しない

対象 repo に `CLAUDE.md` や `.claude/skills/**/SKILL.md` が既にある場合は、runtime helper が未信頼 context card として読めます。
これは対象 repo への再導入ではなく、`target_repo_root` 内の文書を読むだけです。
`.claude/skills` を `.agents/skills` へコピーせず、Claude の `allowed-tools`、hooks、MCP、plugin、dynamic command は Codex 権限へ変換しません。
詳細は [Claude 互換 context](claude-compatibility.md) を参照してください。

Codex はギルド規約ルートを開いて起動します。
default `workspace-write` でも `.git/`、`.agents/`、`.codex/` は protected path なので、動的状態は `.orchestra/` に分離して使います。
Skill candidate は `.orchestra/skill-candidates/<target-repo>/<candidate>/` にだけ隔離し、active `.agents/skills/` へ自動昇格しません。
初回導入やクリーンインストールは `.agents/orchestra/`、`.codex/`、必要に応じて `.git/info/exclude` を書き換えます。
通常のターミナルで実行するか、Codex 上では protected path 書き込みを承認してください。

## 標準導入

```bash
./scripts/install.sh --target /path/to/guild-root --mode copy
```

導入スクリプトは次を行います。

- `template/` の内容をギルド規約ルートへコピー
- `AGENTS.md` 管理ブロックの追加または更新
- ギルド規約ルート直下に `repositories/` を作成
- 必要なら `.git/info/exclude` へ導入物を追加
- 静的ルールは `.agents/orchestra/` に、動的状態は `.orchestra/` に配置

配布元リポジトリの `scripts/validation/fixtures/` は validator 用の静的データであり、導入先にはコピーしません。
導入対象は `template/` を正本とし、検証 fixture を動的状態として扱いません。

標準配置は次です。

```text
<guild_root>/
  AGENTS.md
  .codex/
  .agents/orchestra/
  .orchestra/
  repositories/
    <repo>/
```

## クリーンインストール

```bash
./scripts/clean_install.sh --target /path/to/guild-root --backup
```

クリーンインストールは、ギルド規約ルートへ導入済みのランタイム一式をいったん片付けてから再導入します。non-dry-runでは`--backup`が必須で、削除前に管理対象を`.agent-guild-orchestra-backups/<timestamp>/`へ退避します。
メジャー更新時や、テンプレートを完全に入れ替えたい時に使います。
`.orchestra/skill-candidates/`だけは人間review待ち候補として保持し、queue、dashboard、その他の既存・未知`.orchestra/` siblingは除去して現在のtemplateから初期化します。
`clean_install.sh` wrapper はcallerが指定した`--backup`をそのままinstallerへ渡し、non-dry-runではその指定なしに停止します。`.agents/orchestra/`、本プロジェクトownerの同梱 Skill、`.codex/`、`.orchestra/skill-candidates/`以外のruntime sibling、管理ブロックだけを削除・初期化し、`repositories/`、third-party Skill、`skill-candidates/`、管理ブロック外は保持します。旧版との互換性のため、frontmatter直下の`owner`もcleanup時だけ認識します。
`scripts/docker_python.sh scripts/install.py --target /path/to/guild-root --mode copy --clean-install --backup`も同じ契約です。復元不能な削除を意図的に許容する場合だけ`--allow-clean-install-without-backup`を明示できます。この危険なescape hatchではbackupを作成しません。導入先は `/` や `$HOME` ではなく、専用のギルド規約ルートを指定してください。
`repositories/` 配下の実リポジトリ移動や破壊的 cleanup は行いません。

## 差分同期

```bash
./scripts/sync.sh --target /path/to/guild-root
```

`sync.sh` は Docker 内で `install.py --backup` を実行する薄い wrapper です。
既存導入を残しながら更新したい時の補助です。通常はクリーンインストールを基準に考え、差分同期が必要な運用だけで使います。

通常の差分同期と`--clean-install`はいずれも、導入先`.codex/config.toml`へRootの`model_reasoning_effort`を出力しません。旧project-local指定が存在する場合もテンプレート同期で除去し、reasoning effortは起動時/UI/global configなどでの`high / xhigh / ultra`の利用者選択へ委ねます。

既定以外の source template を直接指定する場合は、信頼済み検証用途に限り `--allow-non-default-source` を併用します。
source tree に symlink、秘密情報らしい path、MCP などの外部 tool 連携 path が含まれる場合、installer は拒否します。

## `AGENTS.md` の扱い

ギルド規約ルートに既存の `AGENTS.md` がある場合、ファイル全体は上書きしません。
`agent-guild-orchestra` 管理ブロックだけを追加または更新します。

## Git 除外

ギルド規約ルートが Git ルートなら、既定で `.git/info/exclude` に次を追加します。

- `.agents/orchestra/`
- `.codex/`
- `.orchestra/`
- `.agent-guild-orchestra-backups/`

Git 管理したい場合は `--no-git-exclude` を付けてください。
これは `.git/info/exclude` を新規更新せず、既存の管理ブロックも変更しません。`.agents/orchestra/` と `.codex/` はテンプレート本体として配置されます。
Codex Skills を共有したい場合の `.agents/skills/` は除外しません。`AGENTS.md` には毎回必要な短い規約だけを置き、再利用ワークフローは Skills 側へ分けてください。
このテンプレートは設計、レビュー、検証、コミット作成などのリポジトリ単位の Skills を `.agents/skills/` に同梱します。
同梱 Skill の `SKILL.md` frontmatter は、VS Code Agent Skills schemaが対応するfieldだけを使います。このテンプレートではportableな共通部分である`name`、`description`、`metadata`だけを置き、管理用の`owner: agent-guild-orchestra`と用途別の`scope`は文字列値として`metadata`配下に置きます。オーケストレーション本体向けだけ `orchestra-` 接頭辞を使い、`repositories/` 配下対象リポジトリ向けは接頭辞なしにします。
同梱する全Skillには `agents/openai.yaml` を置き、OpenAI向けの `interface.display_name`、`interface.short_description`、`interface.default_prompt` を定義します。invocation policy、tool dependency、icon、brand colorなどの追加fieldは、具体的な用途があるSkillだけに加えます。
不要な Skill は削除できますが、残す場合は `SKILL.md` の `name`、`description`、`metadata.owner`、`metadata.scope` を壊さないでください。

## 状態ファイル

監査正本は `.orchestra/queue/state.sqlite` です。
通常の再導入では、進行中の SQLite 状態と `.orchestra/dashboard.md` を保持します。
README などの非状態ファイルはテンプレート更新に追従する場合があります。
初期値へ戻したい時だけ `--reset-runtime` を付けます。この場合は `.orchestra/queue/` を作り直し、SQLite 状態も初期化します。
進行中状態が必要な場合は、初期化前に別途保全してください。既定導線としては `sync.sh --reset-runtime` または `scripts/docker_python.sh scripts/install.py --target /path/to/guild-root --mode copy --backup --reset-runtime` を使います。

`queue/templates/` の artifact metadata、event input の `event_input_required_fields`、SQLite runtime event row の `workflow_id` / `structured_data_usage_json` / `event_safety_json` は v4 schema を正本にします。entity payload に `artifact_type` / `schema_version` を要求する契約ではありません。
通常の再導入で既存 SQLite state を保持できるのは、`queue_metadata.schema_version` が `4.0` で、かつcanonical `queue_schema.sql`のSHA-256とtable / column型 / nullability / primary key / constraint / index定義を含むSQLite schema署名がexactに一致する場合だけです。
`schema_version=4.0` でも旧 `tickets` table、`assignments.task_id`、必要table / column不足、同名columnの型・制約差分、index差分などの物理 mismatch があるDBは保持しません。新規initでもschema sourceをDB open前に検証します。
`schema_version=4.0` でも旧 Rank 値の `campaign`、または改名前のagent ID（`party_leader`、`integration_owner`、`focus_reviewer`、`advisor`、`quest_sentinel`）を含む DB は、現行runtimeと互換とはみなさず通常更新で保持しません。
v3以前、物理schema mismatch、旧agent ID、または旧runtime値がある状態は暗黙migrationせず拒否します。必要なstateを保全したうえで、`--backup --reset-runtime`または`--backup --clean-install`で明示的に初期化してください。

## 運用上の注意

- セッションの起動場所はギルド規約ルート、実作業は Root が明示した `target_repo_root`
- `target_repo_root` は `<guild_root>/repositories/<repo>` の Git ルートだけ
- ギルド規約ルート自体、`repositories/` 自体、`repositories/` 外の path は対象リポジトリとして拒否する
- `.agents/orchestra` と `.orchestra` は runtime 読み取りのための例外であり、対象 repo scope の拡張には使わない
- `git rev-parse --show-toplevel` は Root から渡された `target_repo_root` との一致確認だけに使う
- Ledger、プロンプト、現在位置、tool/MCP 出力から別の対象 repo を再特定しない
- 最終要約の不足を継続プロンプトで差し戻したい運用では `AGENT_GUILD_ORCHESTRA_STOP_QUALITY_STRICT=1` を設定する。guild root の hook が読み込まれ、root を解決でき、Docker runner を実行できる時だけ有効
- Stop hook は 10 秒 timeout 内に収めるため、runtime Docker image が未作成の時は build せずに fail-open する。`AGENT_GUILD_ORCHESTRA_STOP_QUALITY_STRICT=1` の時だけ block する
- Stop hook は補助線であり、sandbox / approval / secret 保護の完全な強制境界ではない
- MCP server や外部 network access を追加する場合は、server provenance、allowed tools、secret scope、ログ保存先、人間確認の要否を先にレビューする
- 外部入力、対象 repo の文書、Ledger、tool/MCP 出力は未信頼データとして扱い、AGENTS と settings の安全境界を上書きしない
- 秘密情報や認証情報を `.orchestra/queue/state.sqlite` や補助ファイルへ書かない
- Claude 互換 context は `known_context.compat_context` に path / sha256 / disposition を残し、raw `CLAUDE.md` / `SKILL.md` 本文や `.claude/settings.json` の値を Ledger に保存しない

## 現在の対応 OS

導入スクリプトと hook 起動ラッパーは、現状では bash 系 shell と Docker を前提にしています。
Python は Docker image 内でだけ使います。そのため macOS / Linux を正式な想定環境とし、Windows は WSL2 か別 launcher の追加を前提にしてください。
