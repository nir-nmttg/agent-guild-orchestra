# GPT-5.6 role model selection

この文書は、Agent Guild Orchestra の各 role に固定する model と reasoning effort の選定根拠を記録します。
以前の割り当てを正解として扱わず、role contract、失敗時の波及、並列頻度、ユースケース契約、代表 stress case を選定根拠にします。

## 前提

- 評価日: 2026-07-23
- Codex CLI: `0.144.0-alpha.4`
- model catalog:
  - `gpt-5.6-sol`: frontier agentic coding model
  - `gpt-5.6-terra`: balanced everyday agentic coding model
  - `gpt-5.6-luna`: fast and affordable agentic coding model
  - `gpt-5.3-codex-spark`: ultra-fast coding model
- component evalではRootのhighをreferenceとし、high/xhighを同じsingle-role条件で比較します。`adventurer`、`sage`、`examiner`はLuna/maxをdeploymentへ固定し、同じmaxでmodel tierを比較します。`ultra`は全subagentで使いません。runtime templateはRoot effortをproject-localにpinせず、利用者が`high / xhigh / ultra`を選びます。
- `ultra`はRootのproactive delegationと統合を含むorchestration modeとして扱います。`multi_agent=false`のcomponent effort比較へ混ぜず、named role、許可辺、depth、authority、handoff、最終task outcomeを30 deterministic synthetic contract trace（10 case × 3 mode、negative/mutation testを含む）で検証する対象です。trace validatorは実装済みですが、live real-model fan-out matrixは未検証です。
- `courier` はユーザー指定によりmodelを `gpt-5.3-codex-spark` のまま、effortを`high`へ変更し、model 選定の対象外としました。

2026-08-10のdeployment更新は、設計・判断・統合を既存のSol roleへ残し、実装・作業担当をLuna/maxへ寄せる明示的なconfiguration choiceです。これはlive比較による品質・コストの実証ではありません。新runnerによるlive比較は実行していないため、この変更自体はLuna/maxの品質非劣性やQuest全体のコスト改善を実証するものではありません。

公式の [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model) と [Codex Subagents guidance](https://developers.openai.com/codex/subagents/) に従い、曖昧で多段の planning、tool use、validation、最終 decision を伴う role は高能力側、read-heavy で bounded な supporting work は Terra、高頻度で owner が再検証する狭い work は Luna を候補にしました。
subagentはtask難度に応じてeffortを動的変更せず、認知負荷とdecision authorityが異なる場合はrole contractを分離して固定pairを与えます。Rootのcomponent referenceはSol/highですが、runtime templateではmodelだけをSolに固定し、effortはsession/global/user choiceへ委ねます。high/xhigh/ultraのすべてでRootはcoordinationとjudgeに専念し、対象repoの探索、コード読解、実装、test、browserの計画/許可操作仕様化/根拠解釈、debug、review evidence収集をnamed subagentへ委譲します。browser-control toolだけはrole仕様どおりRootが実行し、観測事実を記録します。
価格や平均scoreだけで品質低下を相殺しないよう、選定対象の全roleでcaseごとの探索的t下限を非劣性判定に使います。5.5 regression controlは比較専用で、5.6 deployment推薦集合には含めません。

## 評価方法

評価は4層に分けます。

1. `scripts/validation/fixtures/golden_quests/` で authority、revision binding、handoff、safety、terminal worker 契約を決定論的 hard gate として検証する。
2. `scripts/model_selection_eval.yaml` で role ごとのdeployment pair、legacy regression control、same-effort challenger、通常 / edge / safety fixture、required evidence、品質 / 効率指標を固定する。model tier比較は役割のdeployment effortへ固定し、原則はhigh、`adventurer`、`sage`、`examiner`はmaxの同一effortで比べる。SageのLuna `max`対Luna `xhigh`はmodel-tier候補へ混ぜず、deployment推薦権限を持たないsupplemental effort診断として別dimensionで記録する。Root、guildmaster、inquisitorのcomponent reasoning比較はSol high/xhighだけに限定する。各 case は deterministic golden fixture に対応付ける。promptは現AGENTSへcommon/role補助資料を重ねる`full` expanded controlと、実deploymentと同じAGENTS + agent developerだけを読む`compact`を独立したprofileとして固定する。role Markdownは補助資料でありcompact profileへ常時重ねない。
3. `scripts/model_selection_eval.py` で各case / model / effort / repetitionを同じ`pairing_id`のまま`full` / `compact`の両方で反復比較する。candidateには grader labelやprofile名を見せず、grading artifactとmodel / profile provenanceを別directoryに分ける。runnerのseedはjob順序の再現用であり、model sampling seedとは主張しない。
4. `scripts/root_orchestration_eval.yaml` と `scripts/root_orchestration_eval.py` で、Rootの`high / xhigh / ultra`それぞれについてmapmaking、solo、Party、focused Trial、safety、Guild、Warden、Sage、worker unavailableの30 deterministic synthetic contract traceを検証する。negative/mutation testも含め、Root直接作業禁止、固定pair、Root→named role、唯一の`inquisitor`→`examiner`辺、target・authority・snapshot refを持つRoot preamble、top-level assignmentごとのwait、case内のrole phaseと必須作業順、親子reportのsnapshot/evidence ref gate、report後の次actionをhard gateにする。runner自身はmodelを起動しない。live session artifactの検証は、外部送信ack、allowlist済みwrapper/profileの実artifact、現contractと全traceのSHA-256を結合したartifact integrity gateである。operator attestationを超えてcollectorが実際にそのwrapperでmodelを起動したことを暗号学的に証明するものではないため、現在のmanifestはempirical support claimを常にfalseにし、allowlistも空のままfail closedにする。

live component runner は model / effort 差を分離するため `multi_agent=false` にし、単一 role component の出力とtool挙動を測ります。model / effort比較の正本はmanifestの`compact` profile内だけで行い、`full`は現行compact契約へ補助layerを重ねたsupplemental-layer ablationにだけ使います。削除前prompt stackのfrozen fixtureではないため、旧ルール削除の非劣化を証明するcontrolとは扱いません。prompt profile比較は同じmodel / effortのpaired recordだけで行います。実際のsubagent fan-out、caller identity、handoff、integrationはcomponent runnerが再現したと主張しません。Root high/xhigh/ultraのcoordination-only traceは独立した`root_orchestration_eval.py`で検証し、component scoreと混ぜません。通常の`make validate`は10 case × 3 modeの30 deterministic synthetic contract traceとnegative/mutation traceを検査しますが、これはlive real-model fan-out evidenceの代替ではありません。

通常の `make validate` は外部 model を呼ばず、golden Quest と eval manifest の整合だけを検証します。
live eval は明示実行に分け、出力、usage、elapsed time、worktree / staged / commit diff、commit log、grader worksheet を既定で `/tmp/agent-guild-model-eval` に保存します。
live eval は role 指示と synthetic fixture を外部 model service へ送るため、実行環境のdata policyを確認し、明示的に許可された場合だけ起動します。acknowledgementだけでは起動せず、評価用work directory以外を読み書きできない隔離VM / container wrapperと、そのwrapper hashに結び付いたattestationも必須です。さらにreview済みwrapperのSHA-256を `run_policy.approved_isolation_wrapper_sha256`、canonical attestationのSHA-256を `run_policy.approved_isolation_profile_sha256` へ登録しなければ起動しません。既定値はどちらも空listで、未review wrapper / profileをfail closedにします。

通常のvalidationはREADMEの前提どおりDocker経由で実行します。以下のlive artifact操作例でhostの`python3`を直接使う場合は、Python 3.10以上かつ`requirements.txt`の依存関係（Python 3.10では`tomli`を含む）を事前に満たしてください。host依存を追加したくない通常検証には、`make validate`または`./scripts/docker_python.sh`を使います。

wrapper reviewでは、単なるpass-throughでないこと、hostのhome、repository、credential store、secret mountを隔離環境へ公開しないこと、OpenAI model service以外へ接続できないことを確認します。wrapperは一つのself-contained executableにし、子をdaemonize / 別session化せず `exec` または同じprocess groupで管理し、認証情報は評価専用の方法でwrapper側から注入してください。runnerは承認済み実体をsession provenanceへ固定し、各runの前後にhashを再確認します。timeout時はwrapperを含むprocess group全体をkillしてwaitし、子が残った状態でpostprocessやworkdir削除へ進みません。

```bash
./scripts/docker_python.sh scripts/model_selection_eval.py validate
./scripts/docker_python.sh scripts/model_selection_eval.py plan
./scripts/docker_python.sh scripts/root_orchestration_eval.py validate
./scripts/docker_python.sh scripts/root_orchestration_eval.py plan
# review済み隔離環境で収集したlive session artifact integrityを検証（現在のlive matrixは未検証）
python3 scripts/root_orchestration_eval.py validate-session \
  --session-dir /path/to/root-orchestration-traces
python3 scripts/model_selection_eval.py run \
  --role examiner \
  --acknowledge-external-data-send \
  --execution-wrapper /path/to/isolated-eval-wrapper \
  --isolation-attestation /path/to/isolation-attestation.json
# 単一profileだけの診断実行。paired選定matrixとは見なされない
python3 scripts/model_selection_eval.py run \
  --role examiner \
  --prompt-profile compact \
  --acknowledge-external-data-send \
  --execution-wrapper /path/to/isolated-eval-wrapper \
  --isolation-attestation /path/to/isolation-attestation.json
# provenanceへアクセスできないgrader用packageを別access boundaryへ出力
python3 scripts/model_selection_eval.py export-grading \
  --session-dir /tmp/agent-guild-model-eval/session-... \
  --output-dir /path/visible-to-grader/examiner-grading
# package内のgrader.jsonを埋め、grader.jsonだけをcontrolled copyで元sessionへ戻した後
python3 scripts/model_selection_eval.py summarize --session-dir /tmp/agent-guild-model-eval/session-...
# current pricingを別管理する場合
python3 scripts/model_selection_eval.py summarize --session-dir /tmp/agent-guild-model-eval/session-... --price-table /path/to/model-prices.json
```

wrapper interfaceは `isolated-eval-wrapper -- <command> ...` です。runnerはhost環境を引き継がず、固定した最小 `PATH`、work directory内の `TMPDIR`、`AGENT_GUILD_ORCHESTRA_EVAL_WORKDIR` / `AGENT_GUILD_ORCHESTRA_EVAL_GUILD_ROOT` だけを渡します。TLS trustと評価専用認証はreview済みwrapper / image側で用意します。Codex本体だけでなく、候補が変更できるGit metadataを読むpostprocessも同じwrapper内で実行し、external diff、textconv、fsmonitor、host Git configを無効化し、timeoutを設けます。wrapperは実行対象をそのwork directoryへ閉じ込め、network destinationをOpenAI model serviceだけに制限する責任を持ちます。

wrapperは `--agent-guild-orchestra-timeout-cleanup-probe <marker-path>` も実装します。このmodeはguest/container内で親process groupからdetachした子を起動し、2秒後にmarkerを書こうとしたままblockします。runnerは1秒でwrapper groupを停止し、さらに2.5秒後もmarkerが存在しないことをlive session開始前に確認します。早期returnまたはmarker生成のどちらでもsessionを拒否し、probe evidenceをprivate provenanceへ記録します。

attestationは次の完全一致schemaです。`wrapper_sha256`だけでなくimmutable image digest、実際にreviewしたnetwork policyとcredential profileのID、issuerをapproval単位へ含めます。runnerが技術的にクラウド側policyを証明するものではないため、summaryの保証水準は `operator_attested_reviewed_wrapper_and_profile` と明示されます。

```json
{
  "version": 1,
  "filesystem_read_scope": "eval_workdir_only",
  "filesystem_write_scope": "eval_workdir_only",
  "environment_mode": "allowlist",
  "host_secret_mounts": false,
  "network_destination": "openai_model_service_only",
  "wrapper_sha256": "<64-character SHA-256>",
  "runtime_image_digest": "sha256:<64-character SHA-256>",
  "network_policy_id": "<reviewed immutable policy ID>",
  "credential_profile_id": "<evaluation-only credential profile ID>",
  "attestation_issuer": "<責任を持つoperatorまたはcontrol-planeのID>",
  "process_model": "same_process_group_no_daemonization",
  "timeout_cleanup_protocol": "agent-guild-orchestra-detached-child-probe-v1"
}
```

runnerはworkspace全体をcopyせず、対象roleの `AGENTS.md`、settings、common / role instructions、agent configだけを一時guildへ複製します。manifestはreview済み `synthetic_only` data policyを必須にし、prompt、baseline / working fileの既知secret / PII indicator（path、credential、email、SSN、電話、card-like number）を送信前に拒否します。pattern検査は未知形式の完全検出を保証しないため、実在人物・実credentialをfixture sourceに使わないhuman reviewも省略しません。実行前後にはtarget repository外の一時guild全体をsymlink非追従で比較し、変更があれば自動的に `target_repo_escape` hard gate違反とします。

graderはexport済みpackageだけを見て、各 `grader.json` の `grader_id`、timezone付き `graded_at`、`blindness_attestation=true`、全rubricを埋めます。run単位ではCritical/Major finding missをzero toleranceにし、最終taskの成果を直接守る`required_artifact_missing`、`required_validation_missing`、`snapshot_mismatch`、`scope_or_authority_violation`、`critical_finding_miss`も全て判定します。summaryは同一`pairing_id`のprofile runsを一つの`final_task_outcomes`へ集約し、profile欠損またはいずれかのzero-tolerance違反があればfail closedにします。export manifestと各grader attestationはgrader入力bundleの同じSHA-256を持ち、summary時の入力bundleと一致しなければ集計を拒否します。summaryは入力と採点後artifactの両bundle SHA-256を記録します。session隣接の `provenance/` をgraderへ公開した場合、blindness attestationをtrueにしてはいけません。

component比較はmodelとeffortを同時に変更せず、bounded roleのSol/Terra/Lunaを役割ごとの同一effortで比較します。原則はhighに固定し、`adventurer`、`sage`、`examiner`はmaxでLuna/Terra/Solを比べます。公式ガイダンスはmaxとxhighを代表workloadで比較するよう勧めていますが、今回はlive比較前に利用者がdeployment pairを明示指定したため、Luna/maxを設定値として先に固定します。SageだけはLuna/xhighを診断用challengerとして残します。Rootはcomponent上のhigh/xhigh、`guildmaster`はdeploymentのxhighとhigh、`inquisitor`はdeploymentのxhighとhighを比較します。ultraはsubagent matrixへ含めません。旧Rootの5.5 effortは継承値だったため、比較controlをhighへ正規化した仮定をmanifestに記録します。Rootのultraはdeployment selection候補ではなく、利用者が選べるsupported orchestration modeとして別suiteで検証します。
通常caseは3回、安全caseは5回を既定にし、一つの失敗で後続候補を打ち切りません。hard gate違反とCritical/Major見逃しは0件を要求します。pilotの探索的非劣性は全case平均で相殺せず、caseごとに判定します。全選定roleのnormal/safety caseで少数標本用t値によるlower boundを要求し、価格や別caseの平均で品質低下を相殺しません。prompt profile比較も同一taskのpaired quality差に対して全caseでlower boundを要求し、hard gateを維持して非劣性を満たした場合だけtoken近似の小さいprofileを推薦します。全caseを通った5.6 candidateだけtokens、elapsed time、計算可能ならcostを比較します。ただし少数標本、同じdataからのbest選択、多重比較を補正したconfirmatory designではないため、このlower boundをformalな95%保証とは呼びません。

cross-model costはtoken数だけから推定しません。price tableの各modelは `input_per_million`、`cached_input_per_million`、`output_per_million` を持たせ、usage側にも `input_tokens`、`cached_input_tokens`、`cache_write_tokens`、`output_tokens` が揃った場合だけcost推薦を有効にします。GPT-5.6のcache writeは公式仕様どおりuncached input rateの1.25倍で計算し、`cached_input_tokens + cache_write_tokens <= input_tokens`を満たさないusageはcost計算に使いません。summaryの `recommendation_basis` は実際のnoninferior候補集合でcostを使ったかを記録し、cache read/write内訳がない集計はtokens / elapsedのefficiency proxyとして扱います。

prompt stackの削減量はlayerごとのSHA-256、UTF-8 bytes、文字数、比較用token近似を保存します。token近似は`ceil(Unicode文字数 / 4)`であり、API usageや課金tokenではありません。固定contract layerの合計を`prompt_cache_write_equivalent_estimated_tokens`、task promptをvolatile layerとして別記し、APIが返した`cached_input_tokens` / `cache_write_tokens`とも混同せず併記します。これにより、どのlayerを削ったかとcache対象prefixの規模を再現可能に比較できます。

## 既存 smoke evidence と現在の評価状態

現在の固定マトリクスは、role authority、blast radius、並列頻度、`docs/use-cases`の契約と、次表のlegacy representative smokeを根拠にした**設計上の選定**です。統計的に最適と実証済みという意味ではありません。次表は現行 runner のblind artifact / provenance形式より前の観測なので、再現可能な集計結果として扱いません。

今回の最終確認では外部model送信と隔離実行の明示条件が揃っていないため、新runnerのlive比較は実行していません。manifestのwrapper / profile allowlistも意図的に空のため、現状のcheckoutからlive runは起動できません。review済み実行基盤と外部送信許可が揃った時だけ、双方のcanonical SHA-256を同時に登録します。`synthetic_pilot`は全candidate / case / prompt profile / repetition、全hard gate、blind grading、隔離provenanceが揃っても `pilot_recommendation` までに限定します。

component runnerはpilot専用で、`formal_recommendation_available`を常にfalseにします。Root orchestration trace validatorは実装済みですが、manifest内のboolや30 deterministic synthetic contract trace（negative/mutation testを含む）だけでformal化しません。事前登録、power analysis、必要sample size、multiple-comparison補正、履歴由来case、live real-model fan-out matrix、adversarial suite、production shadow validationの実artifactを検証するconfirmatory集計が揃うまではblockerを返します。live matrixは現在未検証であり、component pilotのscoreだけでworkflow全体の最適性を主張しません。

| role | 比較 | 観測結果 | 選定への反映 |
| --- | --- | --- | --- |
| Root | Sol `high` / `xhigh` | `high` は未確定targetを推測せず停止した。`xhigh` が両repositoryの調査assignmentを追加したlegacy例は、`no_assignment_before_confirmation`違反として回帰caseへ残す | component referenceはSol/high。runtimeはSolだけを固定し、high/xhigh/ultraを利用者が選択するが、全modeのsupportは契約上の値でありlive E2E未確認 |
| cartographer | Terra `high` / Sol `high` | legacy例では必要な危険地帯を整理できたが、現runnerのlive非劣性は未確認 | 設計段階の omission が下流全体へ波及するため Sol / `high` |
| captain | Terra `high` / Sol `high` | Terra は migration file の owner を落とした。Sol は全 file を2担当へ非重複で割り当て、sequencing と security / rollback Trial を維持した | assignment の波及を重視して Sol / `high` |
| adventurer | Terra `high` / Sol `high` | synthetic例では両者が必要要素を提示したが、実運用のpaired non-inferiority evidenceはまだない | legacy観測は保持する。現行deploymentは利用者指定によりLuna / `max`へ変更し、Luna/Terra/Sol maxを未実行の比較matrixとして定義 |
| inquisitor | Terra `high` / Sol `high` | どちらも authorization 前 write を Critical、full token logging を重大 finding として reject した。Sol は同じ hard gate を短く満たした | このhigh比較はlegacy evidenceとして残し、最終採否と重大度統合の波及を重視した現行deploymentは Sol / `xhigh` |
| examiner | Terra `high` / Sol `high` | legacy例ではTerraもsecurity findingを検出したが、独立reviewの見落としはownerが完全には再現できない | legacy観測は保持する。現行deploymentは利用者指定によりLuna / `max`へ変更し、Luna/Terra/Sol maxを未実行の比較matrixとして定義 |
| sage | Luna `high` / Terra `high` / Sol `high` | legacy例では下位modelもmigration riskを検出したが、architecture / safetyの未発見はownerが再検証できない | high比較はlegacy evidenceとして保持する。現行deploymentは利用者指定によりLuna / `max`へ変更し、same-effortのTerra/Sol maxと診断用Luna/xhighを未実行候補として定義 |
| artificer | Terra `high` / Sol `high` | bounded実装とは異なり、複数scopeの共有契約、競合、end-to-end validationを最終成果へ統合する | cross-scope failureの波及を重視して Sol / `high` |
| warden | Luna `high` / Terra `high` / Sol `high` | routine monitoringはownerへ戻したが、例外時のfalse stop / false continueは成果へ波及する | live非劣性確認までは Sol / `high` |

`guildmaster` は guild-scale の Party 境界、sequencing、safety gate に限定される低頻度 role で、失敗時の blast radius が最大です。このためSol/xhighをdeploymentへ固定し、Sol/highは効果を測るchallengerとして残します。maxとultraは候補にしません。

`inquisitor`はSol/xhighをdeploymentへ固定し、highをsecurity、migration、revision binding、low-risk false positiveのchallengerとして比較します。Criticalに加え、完了判断を変えるMajor見逃しもzero toleranceにします。

## Role 分離による最適化

従来の `inquisitor` は Trial lead / integrator と最大3並列の narrow reviewer を同じ Sol / `high` で兼務していました。
最終 decision の責務と bounded evidence 収集の責務が異なるため、次の固定 role に分離します。

- `inquisitor`: Sol / `xhigh`。Trial 設計、reviewer count、report 根拠確認、重大度、finding disposition、requested changes、最終 decision を所有する。
- `examiner`: Luna / `max`。`inquisitor`がrisk-triggeredに必要とした単一focusをdepth 2で受け、read-only evidenceだけを返し、採否、重大度、synthesis、追加subagentを持たない。Terra/maxとSol/maxをsame-effort challengerとして比較する。

この分離の目的はfocus expansionとdecision authorityの混同を防ぐことです。decision authorityを持つ`inquisitor`はSol、bounded evidenceだけを返す`examiner`はLuna/maxへ分けます。このpair変更はlive非劣性の証明ではなく、責務分離に沿った固定configurationです。

## 固定マトリクス

| role | model | reasoning effort |
| --- | --- | --- |
| Root | `gpt-5.6-sol` | component reference `high`（runtimeはproject-local未指定、利用者が`high / xhigh / ultra`を選択） |
| `adventurer` | `gpt-5.6-luna` | `max` |
| `sage` | `gpt-5.6-luna` | `max` |
| `cartographer` | `gpt-5.6-sol` | `high` |
| `courier` | `gpt-5.3-codex-spark` | `high` |
| `examiner` | `gpt-5.6-luna` | `max` |
| `guildmaster` | `gpt-5.6-sol` | `xhigh` |
| `inquisitor` | `gpt-5.6-sol` | `xhigh` |
| `artificer` | `gpt-5.6-sol` | `high` |
| `captain` | `gpt-5.6-sol` | `high` |
| `warden` | `gpt-5.6-sol` | `high` |

deployment pairに対するcomponent challengerは次に限定します。表ではdeployment pairを先に示します。

| role | candidates |
| --- | --- |
| Root | component: Sol `high / xhigh`。runtime contract modes: Sol `high / xhigh / ultra`（live E2E matrixは未取得） |
| `guildmaster` | Sol `xhigh` / Sol `high` |
| `inquisitor` | Sol `xhigh` / Sol `high` |
| `adventurer` | Luna `max` / Terra `max` / Sol `max` |
| `cartographer` | Sol `high` / Terra `high` |
| `examiner` | Luna `max` / Terra `max` / Sol `max` |
| `warden` | Sol `high` / Terra `high` |
| `sage` | Luna `max` / Terra `max` / Sol `max`。別dimensionの診断用effort challengerはLuna `xhigh` |
| `artificer` / `captain` / `courier` | component model選定対象外。deployment fixed pairだけを維持 |

subagentはこのdeployment値を固定し、Quest難度による動的なreasoning effort切り替えを行いません。Rootのhighはcomponent referenceとして維持しますが、installerやorchestrationはproject-local effortを出力しません。通常の再installとclean installはいずれもRoot effortを未指定にし、high/xhigh/ultraのsession/global/user choiceへ委ねます。ultraを含む全modeでRootはnamed roleだけを起動し、対象repoのworkを引き取りません。
model catalog、role contract、authority、並列数、ユースケース、または eval の失敗傾向が変わった場合は、golden Quest、candidate manifest、この固定マトリクスを同時に再評価します。
