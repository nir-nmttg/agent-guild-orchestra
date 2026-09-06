# 変更履歴

このプロジェクトの主な変更を記録します。各versionの日付は公開時に確定し、Git tagとGitHub Releaseで記録します。

## [Unreleased]

- 更新・子整理の例外/Ctrl-Cで復元し、復元失敗や再中断ではDocker削除後も親に復旧backupを保持。不要なcandidateの一時disk複製を削除
- AGENTS.mdの既存権限と管理fileのmode変更を保持し、旧v2の既知の配布modeも区別
- commitをレビュー済みindex treeへ固定し、同一pathのindex差し替えを検知。Git操作例の不要な宣言項目と固定temporary filenameを整理
- 未追跡directory内のfileをGit guardが誤って拒否する問題を修正し、指定fileだけをstage/commitする回帰試験を追加
- Rootのeffort選択とLuna/max指定の説明を分離し、通常のlocal Git操作だけではInquisitorを追加起動しないよう指示を明確化
- 評価器で品質不合格と実行失敗を区別し、Rootのmodel照合とeffort別集計を追加
- maintainer用のCodex実機確認scriptを追加。親/子の設定・Skill探索と実spawnを分けて検証し、観測できないmodel/effort/permissionを成功扱いしない
- 非Git親を標準導入先に戻し、設定rootと実Git rootを分離。子repositoryのfile/index/config/ignore ruleは通常導入・更新で変更しない
- 親manifestをschema 2にし、旧親の未変更配布物だけをhashで確認して退避。独自設定・変更済みfile・第三者Skillを保持
- 子側v3からの移行用に、明示された子の未変更・未追跡配布物だけを扱う独立したDocker cleanupを追加
- 親起動のCodex設定・AGENTS・Skill探索を実測し、Git境界による探索停止とnamed agent実行の未確認範囲を文書化

- `install.sh`、`sync.sh`、`make validate`をDocker内のPython 3.12とGitで実行し、ホストPythonのversion依存を解消
- Docker launcherの引数・mount検証と、実Dockerによる導入・更新・旧Guild root移行・linked worktreeのsmoke testを追加

## [3.0.0] - 2026-09-05

### Breaking changes

- productをCodex向けの静的template distributionへ再設計し、独自scheduler、SQLite queue、inbox、Ledger、dashboard、rank、Stop hook、二重settingsを削除
- Rootをgpt-6-astra / highへ変更し、利用者のsession overrideを尊重。Rootの直接作業を許可し、custom agentをLuna/maxのAdventurerとAstra/high read-only Inquisitorだけに縮小
- targetを任意のexplicit canonical Git rootに変更し、guild_root/repositories directory形状の要件を削除
- statelessなboundary_guard.py、snapshot_digest.py、git_guard.pyへ安全確認を集約。repository指定content filterを実行せず、結果・review・acceptance checkのevidence参照を実objectへ結合
- default Skillをdesign-review、verify-change、local-git-operations、github-publish-change、interactive-browser-researchの五つへ整理。maintainer二つとoptional二つは明示選択packageへ分離
- installerをper-install hash manifest、全path preflight、no-op保持、staged transaction、失敗時restoreへ変更。v2からは--major-upgradeと必要に応じた明示的な--legacy-rootで旧managed runtimeをcold archiveし、import / replayしない
- 旧role / Skill aliasと旧runtime import compatibilityを削除
- model評価をAstra-only、Astra+Luna/max、frozen v2.4 baselineのwhole-task accounting protocolへ置換。offline fixtureはschema smokeに限定し、live品質・費用削減を主張しない

## [2.4.0] - 2026-08-23

### 変更

- RootのGPT-5.6 full supported context window（1,050,000 tokens）をtemplateへ固定し、全custom subagentへ200,000-token early compactionを追加
- Rootのfull windowとsubagentのbounded working setをinstaller、validator、deployment文書へ同期
- `VERSION`を`2.4.0`へ更新
- この変更は明示的な構成選択であり、live behavioral/quality/cost comparisonは実施していません（live品質・コスト比較は未実施）

`VERSION`は`2.4.0`です。Rootのfull contextとsubagentのearly compactionを追加する後方互換のminor updateです。

## [2.3.0] - 2026-08-22

### 変更

- `courier`の固定pairを`gpt-5.3-codex-spark / high`から`gpt-5.6-luna / high`へ変更
- `courier`のauthority、Git操作allowlist、snapshot照合、停止条件は変更せず維持
- template、settings、installer、model-selection/root-orchestration eval、validator、deployment文書を`courier`のLuna/highへ同期
- `VERSION`を`2.3.0`へ更新
- この変更は明示的な構成選択であり、live behavioral/quality/cost comparisonは実施していません（live品質・コスト比較は未実施）

`VERSION`は`2.3.0`です。courierの固定pairとrelease validationの整合を取るminor updateです。

## [2.2.0] - 2026-08-13

### 変更

- 実装・作業担当として位置付ける`adventurer`、`sage`、`examiner`の固定pairを`gpt-5.6-luna / max`へ変更
- 設計・判断・統合を担う既存Sol role pair、Root設定、role topology、authority境界は維持
- `courier`のmodelは`gpt-5.3-codex-spark`のまま、reasoning effortを`xhigh`から`high`へ変更
- installer、orchestration/model-selection eval、validator、deployment文書を新しい固定pairへ同期。この割り当ては明示的な構成選択であり、新たなlive品質・コスト実証ではないことを明記

`VERSION`は`2.2.0`です。runtime schema、role topology、authority境界、依存関係、migration、deploy、公開APIは変更しない後方互換のminor updateです。

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
