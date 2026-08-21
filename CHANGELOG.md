# 変更履歴

このプロジェクトの主な変更を記録します。各versionの日付は公開時に確定し、Git tagとGitHub Releaseで記録します。

## [Unreleased]

現在、記録対象の変更はありません。

## [2.2.0] - 2026-08-22

### 概要

- 実装・作業担当として位置付ける`adventurer`、`sage`、`examiner`の固定pairを`gpt-5.6-luna / max`へ変更
- 設計・判断・統合を担う既存Sol role pair、Root設定、role topology、authority境界は維持

### 変更

- `courier`の固定pairを`gpt-5.6-luna / high`へ更新
- `template/.codex/agents/courier.toml`、`template/.agents/orchestra/config/settings.yaml`、関連する検証・evaluation定義を更新
- `VERSION`を`2.2.0`へ更新
- installer、orchestration/model-selection eval、validator、deployment文書を固定pairへ同期。この割り当ては明示的な構成選択であり、新たなlive品質・コスト実証ではないことを明記

`VERSION`は`2.2.0`です。モデル固定pairとrelease validationの整合を取るためのminor updateです。

## [2.1.0] - 2026-08-03

### 概要

- 設計案と実装計画をhandoff前に最小十分で検証可能な状態へ収束させるterminal convergence gateを追加
- reviewとTrialの再開条件を重要な根拠変化へ限定し、処置済み領域やnonblocking Minorによる不要なloopを抑制
- 人間向けの説明を、結論と理解に必要な前提から分かりやすく提示する出力原則を追加

### 変更

- 各設計要素をfixed success criterionまたは観測根拠のあるconcrete risk mitigationへ対応付け、根拠のない将来抽象化・拡張は削除または別contractへ分離
- `request_changes`を未達のfixed success criterion、authority／scope／safety invariant違反、対象検証を妨げる失敗、再現可能なCritical／Majorへ限定
- 再Trialを未解決blocking finding、material risk-surface delta、new material evidenceの影響領域へ限定
- overdesign convergenceのgolden quest fixtureとvalidationを追加

`VERSION`は`2.1.0`です。runtime schema、model routing、installer、依存関係、公開APIは変更しない後方互換のminor updateです。

## [2.0.0] - 2026-07-26

### Breaking changes

- RootをSol固定のcoordination / judge専任とし、対象repoの探索、コード読解、実装、test、browserの計画/解釈、debug、review evidence収集をnamed subagentへ必ず委譲。browser-control toolだけはrole仕様どおりRootが実行して観測事実を記録
- Rootのreasoning effortをproject-localへ固定せず、利用者選択の`high`、`xhigh`、`ultra`を同じnamed-role topologyでサポート
- deployment pairを役割のauthorityとblast radiusに合わせて再編し、`adventurer`と`examiner`をTerra/high、`sage`をLuna/xhigh、`inquisitor`をSol/xhighへ変更。CourierはSpark/xhighを維持
- xhigh roleのjob timeoutに必要な余裕を確保するため、`job_max_runtime_seconds`を1800秒から2400秒へ延長
- runtime settingsを5.0、SQLite runtime schemaを4.0へ更新。canonical schemaのSHA-256と型・制約・indexを含む物理署名をexact照合し、v3以前または定義が異なるDBは暗黙migrationせずfail closedで拒否して明示的な`--backup --reset-runtime`または`--backup --clean-install`を要求
- Root high/xhigh/ultraの30 deterministic synthetic contract trace（10 case × 3 mode、negative/mutation testを含む）を検証する独立harnessを追加し、固定pair、許可edge、target・authority・snapshotの事前確認、assignment wait、role作業順、親子report gate、Root直接fallback禁止をhard gate化。live real-model fan-out matrixは未検証であり、synthetic traceはlive E2E証跡を主張しない

`VERSION`は`2.0.0`です。互換性を維持しないmajor updateとして、旧runtimeを残した差分同期ではなく、必要なstateを保全したうえでの明示的な初期化を前提にします。

## [1.1.0] - 2026-07-14

### 概要

- GPT-5.6向けcompact kernelとrisk-based Guild workflowを整備
- helper-issued snapshot、queue lineage、runtime schema v3の検証を強化
- role別model selection評価、ユースケース、導入・検証スクリプトを提供

### 追加

- OSS運営文書、Issue・Pull Request template、CI、Dependabot設定
- MIT Licenseの日本語参考訳と第三者依存関係の確認用inventory
- 全pathとCODEOWNERS自身を`@nir-nmttg`へ割り当てるCODEOWNERS設定

### 変更

- プロジェクト名を`agent-guild-orchestra`（Agent Guild Orchestra）へ変更し、GitHub URL、配布物の識別子、画像ファイル名を更新
- READMEを日本語中心の公開用ドキュメントとして拡充
- READMEの導入・通常更新・安全なクリーンインストール・復元・運用保護の手順を整理
- Contributionのreview・merge要件、自己承認禁止、単独maintainer時のstrict運用条件と緊急bypass方針を明文化
- `.gitignore`へ秘密情報、Python環境、coverage、build成果物を追加

`VERSION`は`1.1.0`です。`v1.1.0` tagと[GitHub Release v1.1.0](https://github.com/nir-nmttg/agent-guild-orchestra/releases/tag/v1.1.0)は2026-07-14に公開されました。
