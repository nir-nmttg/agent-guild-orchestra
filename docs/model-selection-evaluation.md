# モデル選択評価

この評価は、同じタスクの評価基準と検証を使い、実際のモデル構成ごとの結果をパイロット/ホールドアウトで記録します。既存v3記録ではAstra/highのRoot、Luna 5.6 / maxのワーカー、独立Astra / xhighレビュアーを使います。混合条件`mixed-luna-v1`と`mixed-luna-v2`では、RootのAstra推論レベルを許可リストから選び、Adventurer / Scholar / VerifierにGPT-6 Luna / max、SentinelにGPT-6 Sol / xhighを割り当てます。Inquisitorはv1がAstra / xhigh、v2がAstra / maxです。どの条件も同じJSONLへ記録できますが、条件IDごとに独立して検証・集計します。

## 既存v3で比較する二つのstrategy

旧v3から維持するstrategyは次の二つです。

1. `astra_only`: Astra/highのRootがタスクを直接実装します。ワーカーは記録しません。リスクのあるタスクだけ独立Astra/xhighレビューを付けます。
2. `astra_luna`: Astra/highのRootが必要と判断した時だけLuna/maxワーカーへ委譲します。ワーカー数はタスクごとに可変で、同じタスクで独立ワーカーを複数記録できます。リスクのあるタスクのレビューは独立Astra/xhighです。

各タスクの`features`、`risk`、`review_required`はマニフェストで先に固定します。実際にワーカーやレビューを呼んだか、再試行したか、実行段階の順序は記録へそのまま残し、構成から固定しません。Rootの利用者による推論レベル上書きは`provenance.root_override`とRootの実行段階の有効な`model` / `reasoning_effort`へ記録します。Rootモデルは比較条件のマニフェスト指定（Astra）から変更せず、Lunaワーカーと独立レビューのモデル/推論レベルも固定します。一つの記録内ではRootモデル/推論レベル条件を再試行の間でも変えず、変更する場合は別の実行/記録として扱います。集計は有効なRootモデル/推論レベルごとにグループを分け、異なるRoot推論レベル条件を混ぜません。

`model_selection_eval.yaml`の`profiles`は、`solo`（子枠1）、`current3`（役割追加前の旧構成、子上限3）、`split3`、`split4`、`split6`、`split8`、`flow3`を定義します。`solo`でも高リスクタスクには独立Astraレビューを記録するため、子枠は0ではありません。結果行の任意フィールド`profile`はこの値に、任意フィールド`run_id`は同じタスク・戦略を反復したときの一意な記録IDにします。旧記録のこれらのフィールドは欠測のまま受理し、集計では`profile: "unknown"`とします。`provenance.run_id`は従来どおり必須で、トップレベル`run_id`がない旧記録ではその値を記録IDとして使います。明示したプロファイルでは、すべての実行段階に`named_role`を付けます。`current3`で使える子役は`adventurer`、`scholar`、`inquisitor`（Rootは`guildmaster`または`root`）で、`verifier`と`sentinel`は`split*`と`flow3`で使います。

`condition_id: "mixed-luna-v1"`と`condition_id: "mixed-luna-v2"`は旧プロファイルと別に宣言した混合モデル条件です。各イベントに`named_role`が必須で、Root / GuildmasterはGPT-6 Astra、推論レベルは`low`、`medium`、`high`、`xhigh`、`max`、`ultra`から選びます。評価器の基準値`high`と異なる推論レベルは`provenance.root_override = true`として記録します。Rootの通常運用では`xhigh`を推奨し、両条件で利用者が推論レベルを選べます。Adventurer、Scholar、VerifierはどちらもGPT-6 Luna / max、SentinelはGPT-6 Sol / xhighです。Inquisitorはv1がGPT-6 Astra / xhigh、v2がGPT-6 Astra / maxです。Rootのモデル、条件ごとのInquisitorのモデル/推論レベル、各役の会計ロールは変更できません。マニフェストはv1、v2の片方または両方を宣言でき、宣言された条件IDだけを受理します。旧マニフェストに`conditions`がなくても従来の結果は引き続き処理できますが、混合条件行は受理しません。この条件に`profile`を併記できません。各`condition_id`・Root model/effortの組み合わせについて、分割内の全タスクを要求し、異なるRoot effortの部分行で互いのcoverageを満たすことはできません。

各比較構成のパイロットとホールドアウトでは、役割と割当、Root effort、並列上限、設定/プロンプト/Skillのダイジェスト、受け入れ条件、ホスト、コンテキスト条件をそろえます。Root effortの比較は同じ`condition_id`内の別model/effortグループとして記録します。それ以外の構成条件を変える場合は別の`condition_id`を定義し、来歴ダイジェストを残します。

旧6プロファイルのマニフェストと、`profiles`を持たない旧形式も引き続き受理します。旧v3の戦略・プロファイルに記録済みのモデル割当は変更せず、過去の行を現在のテンプレートや混合条件から再解釈しません。`flow3`や混合条件の結果を扱う場合は、その条件定義を持つマニフェストを使います。未宣言のプロファイルや条件IDは推測して補いません。

### 推論レベルを比べる

RootとInquisitorの変更の効果を分けて調べるため、推論レベル以外のタスク・役割数・並列上限・ホスト・受け入れ条件をそろえ、次の三構成を比較します。

| 比較構成 | condition_id | Rootの推論 | Inquisitorの推論 |
| --- | --- | --- | --- |
| A：評価の基準 | `mixed-luna-v1` | `high` | `xhigh` |
| B：Rootの推論を変更 | `mixed-luna-v1` | `xhigh` | `xhigh` |
| C：今回の推奨構成 | `mixed-luna-v2` | `xhigh` | `max` |

AとBでRootの変更、BとCでInquisitorの変更を評価します。Inquisitorを使うレビュー必須タスクを含め、重大な見落とし・指摘の妥当性・手戻りを確認し、全試行を含む完了時間と使用量を比較します。各構成の結果と根拠を残し、採用する推論レベルの判断に使います。

### 同じ人数で運用を比べる

最初は`split3`を現行の役割分割、`flow3`を部分結果・検証準備・依存関係の優先・粒度調整を加えた運用として比較します。両方とも子上限3で、Rootの有効な推論レベル、Luna/max、受け入れ条件、ホスト、コンテキスト設定をそろえます。`split3`では改善前、`flow3`では改善後の運用プロンプトとSkill一式をそれぞれ固定して使います。`current3`を現在版の基準と取り違えません。プロファイル名だけでは運用を実施した証拠にならず、設定・プロンプト・Skillのダイジェストと実際の委譲・通知・検証の記録を残します。混合条件でも、同じ`condition_id`内の比較では役割数と並列上限、Root effort、設定一式を固定します。

実行順とキャッシュ・ホスト混雑を記録し、同じタスクを新しいコンテキストとクリーンなチェックアウトで反復します。受け入れ合格率、初回合格率、重大な見落としを先に比較し、品質が悪化しない条件で全試行を含む完了時間と使用量を評価します。待ち・手戻りが減った作業種別から適用し、人数の変更は別の比較にします。現行の4タスクだけで全用途への一般化や統計的な非劣性を主張しません。

## パイロットとホールドアウト

まず各条件でパイロットを実行し、タスクの説明、受け入れ条件の評価基準、新しいセッション、権限/モデルの観測、使用量の取得が機能するか確認します。パイロット結果で条件を選ばず、手順を固定してから未使用のホールドアウトを同じ条件へ割り当てます。各タスクはクリーンなチェックアウトと新しいセッションで開始します。

`observed_model_run`には一意の`run_id`、セッション参照、完全な対象リビジョン、Codexバージョン、設定/プロンプト/Skill一式のダイジェスト、新しいコンテキストのフラグ、実際のモデルと権限の証拠を記録します。設定解析だけでは有効なモデル、再ルーティング、権限、新しいコンテキストの証拠になりません。`manual_record`は手入力の記録、`synthetic_fixture`はスキーマ/集計用の合成記録として、観測された実行と明確に分けます。

同じ受け入れ条件の評価基準を、実行者とは別の外部評価者またはブラインドレビュアーが判定します。`grade_refs`は再現可能なテスト、差分、評価成果物を指し、`acceptance_evidence`はマニフェストの受け入れ条件と同じ順序で記録します。`accepted`は全評価条件の結果と一致させます。合成フィクスチャの文字列は実モデルの品質を示しません。

## 記録とタスク全体の集計

JSONLの1行が1つのタスク/評価構成結果です。既存の`task_id`、`strategy`、`split`、`accepted`、`task_input`、`acceptance_evidence`、`provenance`を保持し、`grade_refs`を追加します。`attempts`は1から連番で、各試行は評価基準の結果としての`accepted`、`wall_time_seconds`、`wall_time_source`、実行した`stages`を持ちます。`accepted=false`は、失敗した実行段階と`failure_evidence`を伴う実行エラー、または全実行段階が`completed`でも評価基準を満たさない品質失敗のどちらも記録できます。再試行前の品質失敗も分母から除かず、最終試行の結果は記録の`accepted`と一致させます。

各実行段階は`sequence`、一意の`invocation_id`、`role`（`root` / `worker` / `review`）、有効な`model` / `reasoning_effort`、`status`、`failure_evidence`、`usage`、`elapsed_seconds`、再現可能な`evidence_refs`を持ちます。`named_role`は`guildmaster` / `root` / `scholar` / `adventurer` / `verifier` / `sentinel` / `inquisitor`のいずれかです。明示した旧プロファイルと`condition_id`では必須、旧形式では任意です。Verifier/Sentinelは会計上`worker`、Inquisitorは`review`です。役名を付けた場合は、その行の評価条件に対応するモデル・推論レベルと会計ロールの一致を検証します。旧条件ではVerifier/Sentinelも従来のLuna / max割当を維持し、混合条件では定義に従いGPT-6 LunaとGPT-6 Solへ分けます。`sequence`は記録された実行順を表します。Astra-onlyのワーカーは拒否されますが、Astra+Lunaのワーカー数は0以上です。タスクの`review_required`が`true`なら最終試行へレビューを含めます。並列実行や再試行の数をこのバリデーターが知らないため、実際の全呼び出しを記録する責任は実行担当/Rootに残ります。

子ターンの実時間を測る場合だけ、イベントへ`start_time`と`end_time`をペアで記録します。値は非負の数値、またはタイムゾーン付きISO-8601です。片方だけの時刻、逆順の時刻は拒否します。両方を`null`にするか両方を欠測にした子ターンは受理しますが、他の子ターンだけ時刻があっても、その試行の`max_parallel_child_turns`は`unknown`として集計し、失敗や使用量の分母から行を外しません。集計の`max_parallel_child_turns`は区間の重なりから算出したworker/reviewターンの最大数であり、設定枠の「開いたthread数」ではありません。設定枠を観測できた場合は試行へ`max_open_threads`と`max_open_threads_source`を記録し、別の集計値として出します。既知の時間区間から求めたピークが測定した`max_open_threads`より大きい場合は記録を拒否します。プロファイル上限超過も拒否します。旧記録や不完全な新記録の集計値は`unknown`で、0へ変換しません。

`usage`は`tokens`、ホストから得た`codex_usage`、`api_cost_usd`、各`source`を分けます。欠測は`null`とし、欠測を0へ変換しません。観測したCodex使用量とAPI費用の米ドル推定値・アカウントから報告された費用は別集計で、API価格表、価格DB、費用推定を行う実行機構は持ちません。必要な使用量が一つでも不明なら該当合計は`unknown`で、単純な費用差から費用削減を主張しません。実経過時間も実行段階合計ではなく試行単位で記録します。
出典は証拠種別と一致させます。`synthetic_fixture`は`synthetic`/`unknown`、`manual_record`は`manual`/`unknown`、`observed_model_run`は使用量と実経過時間が`observed`/`unknown`、費用が`account_reported`/`api_estimate`/`unknown`です。これにより、合成値が観測された使用量やアカウントから報告された費用として集計されません。

### 待ち・引き継ぎ・手戻りと重大な見落とし

各試行の任意フィールド`timing_breakdown`を使う場合は、次の3値と`source`をまとめて記録します。値は非負の有限数（秒）、計測できない値は`null`です。`source`は上記の証拠種別に対応した時間の出典です。既知の値がある場合に`unknown`を使わず、全値が`null`なら`unknown`にします（合成フィクスチャでは`synthetic`も可）。計測していない時間を0で埋めません。

| フィールド | 記録する時間 |
| --- | --- |
| `dependency_wait_seconds` | 必要な入力・仕様・他担当の変更を待つため、対象タスクを進められなかった実時間 |
| `handoff_seconds` | 委譲の準備、起動、結果の受領・確認に費やした実時間 |
| `rework_seconds` | 不合格、無効になった部分結果、変更された前提への対応でやり直した実時間 |

同じ分類の並行区間は重なりを一度だけ数え、子の所要時間を足して待ち時間にしません。分類間の重複はあり得るため、3値の和をタスクの実経過時間にしません。各値は、計測済みならその試行の`wall_time_seconds`以下である必要があります。これらは実行履歴から提供する補助計測で、評価器が自動収集する値ではありません。

結果行の任意フィールド`major_miss_count`は、独立した評価者が全試行を通じて確認した重大な見落としの件数です。同じ見落としを複数試行で重複計上せず、修正済みのものも含め、根拠を`grade_refs`へ記録します。値は非負整数または`null`で、省略・`null`は不明です。最終的に合格した実行でも重大な見落としが途中で見つかる場合があるため、`accepted`から推定しません。0は独立評価で見落としがなかった場合だけ記録します。

~~~bash
python3 scripts/model_selection_eval.py --plan
python3 scripts/model_selection_eval.py --validate-results /path/to/results.jsonl
python3 scripts/model_selection_eval.py --summarize /path/to/results.jsonl
~~~

集計は有効なRootモデル/推論レベル、`profile`、`condition_id`ごとにグループを分け、各グループの`run_ids`（反復ID）を保持します。グループを反復IDごとに分割しないため、同じタスクの複数回実行もタスク分母へ残ります。タスク分母を保った合格件数（`accepted`）、試行/実行段階/レビュー/ワーカー件数、役割別usage、出典付きトークン、Codex使用量、API費用、実経過時間、開いたthreadの最大数、子ターンの実並列数を出します。役割別usageは従来の全体usageと併記します。プロファイルも条件IDも付けない旧記録は、splitごとに従来どおり両戦略・全タスクの行を要求します。明示した各プロファイルはその戦略のsplit内全タスクを、明示した各条件IDはその条件の各Rootモデル/effort groupについて戦略のsplit内全タスクを要求します。未使用のプロファイルや条件まで実施することは要求しません。統計的優越、非劣性、費用削減を自動で主張しません。パイロット/ホールドアウトの比較には、同じ検証、外部評価、実際の権限/モデル/新しいコンテキストのイベント、全再試行/失敗、適切なホスト使用量の証拠が必要です。

追加集計も同じグループと全タスクの分母を使います。

| 出力 | 意味 |
| --- | --- |
| `first_attempt_accepted_tasks` / `first_attempt_acceptance_rate` | 最初の試行で全条件に合格したタスク件数と割合。再試行後の合格を初回合格に数えない |
| `task_wall_time_seconds` | タスクごとの全試行の`wall_time_seconds`合計の分布。`known_count`、`unknown_count`、`median`、`p90`、`max`、`basis`、`p90_method`を返す |
| `timing_breakdown` | 各分類の全試行合計と、各キーに`_basis`を付けた出典。ある分類に欠測があればその分類の合計は`null` / `unknown` |
| `major_miss_count` / `major_miss_count_basis` | 独立評価の見落とし件数の合計と出典。一件でも欠測なら合計も不明 |

時間分布には最終的な不合格タスクも含めます。子の処理時間の和ではなく、タスク側で計測した全試行の時間を使います。初期準備、Rootの結果処理、試行間の再委譲、最終判定もいずれか一つの試行に含め、試行間に未計測の空白や重複を作らないよう記録します。中央値は偶数件なら中央2値の平均、p90は昇順の`ceil(0.9 × 件数)`番目（`p90_method: "nearest_rank"`）です。小標本のp90を安定した性能保証とは扱いません。

あるタスクの一試行でも時間が欠測なら、そのタスクを`unknown_count`に数えます。グループに不明なタスクがあれば、観測できたタスクだけで代表値を出さず、中央値・p90・最大値と`basis`を不明にします。既知の0秒は欠測と区別します。`basis`は`observed`、`manual`、`synthetic`、または`unknown`です。旧記録は新しい計測を補完せず受理し、既存の集計項目を維持します。手入力の時間分布は`manual`として記述的に集計し、観測値だけを合計する従来の`total_wall_time_seconds`とは出典の扱いを区別します。

`scripts/validation/fixtures/model_eval_offline.jsonl`は`synthetic`と明記した従来のパイロットフィクスチャです。直接実装・レビューなし、状況に応じた委譲でワーカーなし、重大なリスクのレビュー、複数ワーカー、再試行前の品質失敗、最終品質失敗、誤った役割・モデル・実行順、呼び出しの重複、失敗の証拠の欠落、Root推論レベル上書き、実行を観測した記録の来歴のバリデーター経路を確認します。`model_eval_mixed_condition.jsonl`と`model_eval_mixed_v2_condition.jsonl`はそれぞれv1/v2の合成フィクスチャで、3役のGPT-6 Luna、GPT-6 Sol Sentinel、条件別Inquisitorの記録・割当・集計を検証します。どちらも実際のベンチマークではなく、品質、ホスト割り当て上限、API費用、費用削減の証拠ではありません。
