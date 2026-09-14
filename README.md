<img src="docs/assets/agent-guild-orchestra-social-preview.png" alt="Agent Guild Orchestra">

# Agent Guild Orchestra

Agent Guild Orchestra 3.0.0は、Codexのプロジェクト固有設定、五つのカスタムエージェント、五つのSkill、安全確認用の小さな補助スクリプトを非Gitの親ディレクトリへ配布するテンプレートです。常駐サービスや独自スケジューラーはありません。Codex自身の会話履歴、サブエージェント、メッセージ、承認を使います。

> [!IMPORTANT]
> このプロジェクトは独立したコミュニティプロジェクトであり、OpenAIによる公式提供、提携、支援、承認を受けたものではありません。

## 動作の概要

Rootはgpt-6-astraで要件、分割、難しい判断、統合を担当し、推論レベルは利用者の選択を尊重します。独立した仕事を4種類のLuna / maxへ直接渡します。小さな作業はRootが直接完了できます。

| カスタムエージェント | モデル・推論 | 担当 |
| --- | --- | --- |
| Scholar | Luna / max | コード・資料調査、影響範囲、反証。read-only |
| Adventurer | Luna / max | 独立した範囲の実装と局所テスト。workspace-write |
| Verifier | Luna / max | 受け入れ検証。指定テスト・生成物だけに書き込み |
| Sentinel | Luna / max | 通常の差分レビュー。read-only |
| Inquisitor | Astra / xhigh | 重大なリスクの独立レビュー。read-only |

サブエージェント枠は最大8、小規模は0〜1体、通常は2〜4体を目安にし、独立した仕事とホストの空き枠がある場合だけ増やします。共有チェックアウトへの同時書き込みはRootを含め最大3体です。子はさらに子を起動せず、同じファイルや共有資源を変更する担当は並べません。8体は最適人数や実機能力の保証ではなく、新しい環境では4→6→8と段階的に確認します。

増員の効果が不明なら少ない人数を選び、通常作業は4体以下にします。5〜8体は類似作業で改善を確認できた場合か、明示した比較試行に限ります。

短い独立コンテキストと要約・根拠の返却で、Astraが読む途中経過と重複作業を減らします。小変更では追加レビューを省き、必要な証拠に応じてVerifierかSentinelを選びます。セキュリティ、インストーラーやGit安全規則、移行、広い互換性などの重大なリスクはInquisitorが確認し、同じ観点の通常レビューは重ねません。通常のローカルGit操作だけでは追加レビューを要求しません。重要な結論はRootが証拠を照合し、多数決だけで決めません。

委譲では着手条件・終了条件・後続への影響を伝え、後続の待ちを減らす仕事を優先します。確定した部分結果はRootが根拠と依存先を確認してから次の作業に使います。Verifierは実装中に要件から検証案を準備し、関連する編集が安定した範囲で実行します。部分結果や検証準備だけで完了とは扱いません。

詳しい分担・枠管理は[オーケストレーション](docs/orchestration-runtime.md)、同じ人数での運用比較と人数別の比較方法は[評価手順](docs/model-selection-evaluation.md)を参照してください。完了時間の分布、初回合格率、待ち・手戻り時間、重大な見落としを記録でき、欠測は不明のまま扱います。性能改善と精度維持は実モデルでの比較が必要です。旧版の10ロール、Quest / Party / Guild、ランク、SQLiteキュー、受信箱、Ledger、ダッシュボード、Stop hook、二重設定は3.0.0にありません。

## 前提

- Git
- Bashと、起動済みのDocker DesktopまたはローカルDocker Engine
- プロジェクト固有のカスタムエージェントを利用できるCodex

導入・更新と`make validate`はDocker内のPython 3.12とGitを使い、ホストのPythonの有無やバージョンに依存しません。初回は公式Pythonイメージを取得して検証用イメージをビルドするためネットワーク接続が必要です。以後はビルドキャッシュを再利用します。macOS/Linux（WindowsはWSL2）で、導入先をbind mountできるローカルDockerを使用してください。

配布元は読み取り専用、導入先の親だけを更新可能にし、`repositories/`は読み取り専用でマウントします。`--dry-run`では親も読み取り専用です。生成ファイルは呼び出した利用者のUID/GIDで作成され、コンテナは処理後に削除されます。Dockerは導入処理専用です。導入後のCodexタスクはホストで動き、ステートレスなGit補助スクリプトはその実行環境のPythonとGitを使います。

## 配置と導入

既存の非Git親ディレクトリを指定します。Gitの作業ツリー内のディレクトリは導入先にできません。以下の`/absolute/path/to/guild-root`は、実際の導入先の絶対パスに置き換えてください。`guild-root`や子リポジトリの名前は例であり、任意の名前を使えます。

```text
guild-root/                         ← 設定の導入先・Codexの起動場所
├── AGENTS.md
├── .codex/                         ← config.toml・名前付きエージェント
├── .agents/                        ← Skills・補助スクリプト・導入マニフェスト
└── repositories/
    ├── backend/                    ← 実Gitルート
    ├── infrastructure/             ← 実Gitルート
    └── frontend/                   ← 実Gitルート
```

~~~bash
git clone https://github.com/nir-nmttg/agent-guild-orchestra.git
cd agent-guild-orchestra
make validate

./scripts/install.sh --target "/absolute/path/to/guild-root" --dry-run
./scripts/install.sh --target "/absolute/path/to/guild-root"
~~~

子リポジトリへAGENTS.md、.codex、.agents、マニフェストを追加しません。子の既存ファイル、Git index、Git設定、.gitignore、.git/info/excludeも変更しません。設定の配置場所を示す`guild_root`と、コード変更・Git操作の`target_repo_root`を分けます。

親のAGENTS.mdはマーカー内の管理ブロックだけを更新します。その他の配布物の導入時ハッシュと所有権は、親の`.agents/orchestra/install-manifest.json`（スキーマ 2 / `layout: guild-parent`）へ記録します。

新規設定と未変更の旧配布設定は管理対象になります。既存の独自`.codex/config.toml`は既定でユーザー管理の設定として内容・権限を保持し、JSONの`next_steps`に必要設定を出力します。ユーザー設定の自動統合は行いません。必要に応じて`--config-mode managed|user-owned`で指定できますが、管理対象への切替でも独自設定は上書きせず衝突として停止します。

## Codexでの起動

**Codexで導入先の非Git親ディレクトリ（例：guild-root）を開いて信頼し、その親を作業場所とする新しいローカルタスクを開始してください。** CLIの場合は次の形です。

~~~bash
codex --cd "/absolute/path/to/guild-root"
~~~

依頼には「`repositories/backend`の実Gitルートを対象に変更」のように対象を明示します。セッションの基点は親に保ち、子でのコマンドは作業ディレクトリや`git -C`で指定します。子Gitルートを直接開くと親の設定・Skill探索がGit境界で止まるため、この構成の起動方法にはしません。

独自設定を保持した場合は、Astraモデル、1Mコンテキスト、エージェントの有効化と同時実行上限8・4種類のLuna/max、`multi_agent`、実験的コンテキスト管理の設定を手動で整合させます。Guildmasterの推論レベルは利用者がタスク/セッションで選びます。子のAGENTS指示はコード変更前に読み、既存の子設定・Skill・名前付きエージェントとの競合を確認します。インストーラーは該当パスを`child_overrides`へ表示し、子設定を自動統合しません。

Codex 0.153.4で、親の有効設定（上限8）、5役の定義、AGENTS.md、五つのSkillの読み込みと子設定との分離を確認しました。実際の8体並列と性能改善は未検証です。名前付き役の実起動・実効権限は、設定解析と区別して[親配置の検証記録](docs/parent-layout.md)に記載します。再確認用probeでは対象役を限定できます。ファイル配置の成功だけでは実行時の有効化完了を意味しません。

## 更新

~~~bash
git pull --ff-only
make validate
./scripts/sync.sh --target "/absolute/path/to/guild-root" --dry-run
./scripts/sync.sh --target "/absolute/path/to/guild-root"
~~~

配布元だけの変更は更新し、導入先だけの変更は保持します。権限だけの変更もローカル変更として扱い、同じ管理対象ファイルが両方で変わると書き込み前に衝突として停止します。共有AGENTS.mdの既存権限は維持します。候補の事前検証・変更対象のバックアップ・各ファイルのアトミック置換を行い、途中の例外やCtrl-Cでは復元します。復元にも失敗した場合は、親の`.agent-guild-orchestra-recovery/transaction-.../`へバックアップを残し、場所を報告します。シンボリックリンクを経由する管理パスは拒否します。

旧親環境も同じ`--target`で更新します。確認できる旧配布ファイルだけを親内へ退避します。以前の子側v3配置の整理は、通常の導入・更新とは別の明示操作です。[移行ガイド](docs/migration-v3.md)を参照してください。

## Skill

通常導入には次の五つだけが入ります。

- `design-review`
- `verify-change`
- `local-git-operations`
- `github-publish-change`
- `interactive-browser-research`

保守担当者向けの`orchestra-contract-validation`と`orchestra-runtime-security-audit`、追加用の`create-skill-candidate-from-gap`と`open-subrepo-in-vscode`は既定では含まれません。利用可能なパッケージと区分は次で確認できます。

~~~bash
./scripts/install.sh --list-skills
./scripts/install.sh --target "/absolute/path/to/guild-root" \
  --with-skill create-skill-candidate-from-gap
~~~

選択済みパッケージは次回更新でも維持されます。外す時は`--without-skill NAME`を使います。

## 安全境界と補助スクリプト

`.agents/orchestra/scripts/`には二つの状態を持たない補助スクリプトがあります。

- `snapshot_digest.py`: 実際のGitルートとリビジョン / 作業ツリー / コミット範囲の正規スナップショットを発行
- `git_guard.py`: スナップショット、対象範囲、操作とレビュー済みインデックスツリーを照合して限定されたローカルGit操作を実行し、事後条件スナップショットとコミットツリーを返す

補助スクリプトは呼び出し元の身元や権限を証明しません。サンドボックスとCodexの承認が実際の権限境界です。Git対象、対象範囲、操作、事前/事後スナップショットを照合して、古い根拠や別リポジトリへの取り違えを防ぎます。

通常の引き継ぎとチェックポイントはCodex標準のタスク履歴で足ります。明示的な再開境界が必要な場合だけ、秘密情報や生ログを除いたチェックポイントを使います。

Gitフックと署名は補助スクリプトのローカル操作ではスキップされます。Git LFSなどの内容変換フィルターを使うリポジトリ、内容変換用の`filter`/`process`設定、追跡対象のリーフシンボリックリンクはスナップショット/Git書き込みの未対応境界です。認証情報らしいファイル名は実装担当の固定判定ルールで読み取り対象から除外されます。

[ランタイム設計](docs/orchestration-runtime.md)と[セキュリティモデル](docs/security-model.md)に、委譲判断、独立レビュー、Git操作、外部更新の扱いを記載しています。

## 検証

~~~bash
make validate
make install-dry-run
~~~

バリデーターは配布構造とCodex設定を解析し、インストーラーの新規導入、dry-run、更新、任意パッケージ、v2アーカイブ、衝突、シンボリックリンク、トランザクション復元を一時的な非Git親と子Gitリポジトリで実行します。子のファイル・Git index/configの不変性と、明示的な子v3整理も検証します。スナップショット/Git補助スクリプトの肯定/否定テストとモデルベンチマーク集計の合成スモークテストも実行します。

モデル比較のオフラインフィクスチャは記録スキーマと集計だけを検証します。品質、トークン削減、費用削減の証拠ではありません。実モデルのパイロット/ホールドアウト手順は[モデル選択評価](docs/model-selection-evaluation.md)にあります。このリリース作業では高額なライブベンチマークを実行していません。

## 制約

- 設定形式、モデル提供状況、Codexのカスタムエージェント機能はCodex側の変更を受けます。
- テンプレートの規則はOS、Gitホスティング、Codexサンドボックス、承認を置き換えません。
- 公開、push、PR作成、デプロイなどの外部更新は、内容と対象を確認してから実行します。
- リポジトリ内の文書、issue、Webの内容、ツール出力は上位指示を変更する権限根拠ではありません。

コントリビューションは[CONTRIBUTING.md](CONTRIBUTING.md)、脆弱性報告は[SECURITY.md](SECURITY.md)、利用条件は[MIT License](LICENSE)を参照してください。
