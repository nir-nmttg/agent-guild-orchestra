# モデル選択評価

3.0.0の配布設定はRootがAstra / high、AdventurerがLuna / max、read-only InquisitorがAstra / highです。これは設計上の選択であり、このrelease作業で30 taskのlive比較や費用削減の実証は行っていません。

## 比較する三構成

scripts/model_selection_eval.yamlは次を比較します。

1. astra_only: Astra Rootが実装し、同じAstra/high risk reviewを受ける
2. astra_luna: Astra RootとLuna/max Adventurerで実装し、同じAstra/high risk reviewを受ける
3. v2_4_baseline: tagまたはcommitで固定した2.4.0配布物を使い、当時の実装routingとreview policyをそのまま実行する

最初の二構成は同じAstra/high reviewを使い、implementation strategyの差を比較します。baselineは現在のworking treeから再現した「旧風prompt」ではなく、2.4.0 releaseを別checkoutで実行します。baselineとの比較はreview policyを含むsystem全体の差であり、implementation modelだけの差とは解釈しません。

## pilotとholdout

まずmanifestのpilotだけを各構成で実行し、task記述、acceptance rubric、usage captureが機能するか確認します。pilot結果を見て構成を選びません。手順を固定した後、未使用のholdoutを各構成へ同じ順序または事前に決めた順序で割り当てます。

各taskは独立したclean checkoutとsessionで始めます。live recordの`provenance`にはunique run ID、full target revision、Codex versionを記録します。`task_input`はmanifest objectiveと一致させ、`acceptance_evidence`はmanifestの全criterionを同じ順序で、pass/failと根拠を付けて記録します。modelの実行結果は人間またはblind reviewerが同じacceptance rubricで判定し、recordの`accepted`は全criterionの結果と一致させます。

## whole-task accounting

JSONLの一行が一つのtask / strategy結果です。`attempts`は1から連番のlistで、各attemptにoutcomeと実行されたstageを保存します。再試行前のattemptはfailed stageを含み、最終attemptのoutcomeはrecord全体の`accepted`と一致します。各stageはroot / worker / reviewのrole、実際のmodel、effort、完了状態、token、costを持ちます。Astra-onlyにworker stageを混ぜず、Astra+Lunaでは各attemptのLuna/max worker使用を記録し、両v3構成の最終attemptはAstra/high reviewを含めます。v2.4 baselineも実際に使ったmodel / effortをstageごとに記録します。accepted=falseのtaskも分母から外しません。

tokenとcost_usdは全attemptの全stageについて実runが報告した値を優先します。不明値はnullであり、0ではありません。一つでもstage usageが不明ならgroup totalもnullになります。tokenから費用を推定する場合はcached input、uncached input、output、reasoning、long-context、fast pricingを実行時の公式価格で区別する必要があります。このharnessはdefault価格表から費用を推測しません。

~~~bash
python3 scripts/model_selection_eval.py --plan
python3 scripts/model_selection_eval.py --validate-results /path/to/live-results.jsonl
python3 scripts/model_selection_eval.py --summarize /path/to/live-results.jsonl
~~~

summaryはassigned task、accepted task、attempt、completeな場合のtoken / cost totalを出します。統計的な優越、非劣性、費用削減を自動で主張しません。accepted coverageが異なる構成の単純な費用差は、節約の根拠として扱いません。

scripts/validation/fixtures/model_eval_offline.jsonlはparser、provenance shape、model / role区別、unknown usage、failed / retry accounting、acceptance evidence、accepted-task denominatorを確認するfixtureです。offline fixtureのtarget revisionとCodex versionはnullで、live runを装いません。evidence_kind=offline_fixtureであり、実model品質や費用の証拠ではありません。
