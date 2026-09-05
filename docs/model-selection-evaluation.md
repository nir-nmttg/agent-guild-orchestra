# モデル選択評価

この評価は、Astra-onlyとadaptive Astra+Luna maxの実際のtask結果を、同じtask rubricとverificationで記録するための小さなpilot/holdout手順です。評価armではAstra/highのRoot、Luna Adventurer / max、独立Astra / xhigh reviewerを使います。v2.4 armや固定 worker/review topologyは現行比較の必須条件ではありません。

## 比較する二つのarm

`scripts/model_selection_eval.yaml`のstrategyは次の二つだけです。

1. `astra_only`: Astra/highのRootがtaskを直接実装します。workerは記録しません。risk taskだけ独立Astra/xhigh reviewを付けます。
2. `astra_luna`: Astra/highのRootが必要と判断した時だけLuna/max workerへ委譲します。worker数はtaskごとに可変で、同じtaskで独立workerを複数記録できます。risk taskのreviewは独立Astra/xhighです。

各taskの`features`、`risk`、`review_required`はmanifestで先に固定します。実際にworkerやreviewを呼んだか、retryしたか、stageの順序はrecordへそのまま残し、構成から固定しません。Rootのuser model/effort overrideは`provenance.root_override`とRoot stageのeffective `model` / `reasoning_effort`へ記録します。Luna workerと独立reviewのmodel/effortは固定します。

## Pilotとholdout

まずpilotを各armで実行して、task description、acceptance rubric、fresh session、permission/model observation、usage captureが機能するか確認します。pilot結果でarmを選ばず、手順を固定してから未使用のholdoutを同じ条件へ割り当てます。各taskはclean checkoutとfresh sessionで開始します。

`observed_model_run`にはunique `run_id`、session reference、full target revision、Codex version、config/prompt/Skill bundle digest、fresh-context flag、実際のmodel permission evidenceを記録します。設定parseだけではeffective model、reroute、permission、fresh contextの証拠になりません。`manual_record`は手入力の記録、`synthetic_fixture`はschema/accounting用の合成記録として、observed runと明確に分けます。

同じacceptance rubricを実行者と別の外部graderまたはblind reviewerが判定します。`grade_refs`は再現可能なtest、diff、grade artifactを指し、`acceptance_evidence`はmanifest criterionと同じ順序で記録します。`accepted`は全criterionの結果と一致させます。合成fixtureの文字列は実model品質を示しません。

## Recordとwhole-task accounting

JSONL一行が一つのtask/strategy結果です。既存の`task_id`、`strategy`、`split`、`accepted`、`task_input`、`acceptance_evidence`、`provenance`を保持し、`grade_refs`を追加します。`attempts`は1から連番で、各attemptは`accepted`、wall timeとsource、実行した`stages`を持ちます。再試行前のattemptはfailed stageと`failure_evidence`を含み、最終attemptの結果はrecordの`accepted`と一致させます。

各stageは`sequence`、unique `invocation_id`、`role`（root/worker/review）、effective model/effort、status、failure evidence、usage、elapsed time、reproducible `evidence_refs`を持ちます。sequenceは記録された実行順を表します。Astra-onlyのworkerは拒否されますが、Astra+Lunaのworker数は0以上です。taskの`review_required`がtrueなら最終attemptへreviewを含めます。parallelismやretryの数をこのvalidatorが知らないため、実際の全invocationを記録する責任はrunner/Rootに残ります。

`usage`はtokens、hostから得た`codex_usage`、`api_cost_usd`、各sourceを分けます。欠測はnullとし、欠測を0へ変換しません。observed Codex usageとAPI USD estimate/account-reported costは別集計で、API価格表、price DB、推定runnerは持ちません。必要なusageが一つでも不明なら該当totalはunknownで、単純なcost差からsavingsを主張しません。wall timeもstage合計ではなくattempt単位で記録します。
sourceはevidence kindと一致させます。`synthetic_fixture`は`synthetic`/`unknown`、`manual_record`は`manual`/`unknown`、`observed_model_run`はusageとwallが`observed`/`unknown`、costが`account_reported`/`api_estimate`/`unknown`です。これにより、合成値がobserved usageやaccount-reported costとして集計されません。

~~~bash
python3 scripts/model_selection_eval.py --plan
python3 scripts/model_selection_eval.py --validate-results /path/to/results.jsonl
python3 scripts/model_selection_eval.py --summarize /path/to/results.jsonl
~~~

summaryはtask分母を保ったaccepted count、attempt/stage/review/worker count、source付きtoken、Codex usage、API cost、wall-timeを出します。統計的優越、非劣性、費用削減を自動で主張しません。pilot/holdoutの比較には、同じverification、外部grade、実際のpermission/model/fresh-context event、全retry/failure、適切なhost usage evidenceが必要です。

`scripts/validation/fixtures/model_eval_offline.jsonl`はsyntheticと明記したpilot fixtureです。direct/no-review、adaptive no-worker、material review、複数worker、retry、wrong role/model/order、duplicate invocation、missing failure evidence、root override、observed provenanceのvalidator経路を確認します。これは実benchmarkではなく、品質、host quota、API費用、savingsの証拠ではありません。
