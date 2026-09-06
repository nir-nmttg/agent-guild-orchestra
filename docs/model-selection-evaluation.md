# モデル選択評価

この評価は、Astra-onlyと適応型のAstra+Luna maxの実際のタスク結果を、同じタスクの評価基準と検証で記録するための小さなパイロット/ホールドアウト手順です。評価条件ではAstra/highのRoot、Luna Adventurer / max、独立Astra / xhighレビュアーを使います。v2.4の条件や固定したワーカー/レビュー構成は現行比較の必須条件ではありません。

## 比較する二つの条件

`scripts/model_selection_eval.yaml`の評価構成は次の二つだけです。

1. `astra_only`: Astra/highのRootがタスクを直接実装します。ワーカーは記録しません。リスクのあるタスクだけ独立Astra/xhighレビューを付けます。
2. `astra_luna`: Astra/highのRootが必要と判断した時だけLuna/maxワーカーへ委譲します。ワーカー数はタスクごとに可変で、同じタスクで独立ワーカーを複数記録できます。リスクのあるタスクのレビューは独立Astra/xhighです。

各タスクの`features`、`risk`、`review_required`はマニフェストで先に固定します。実際にワーカーやレビューを呼んだか、再試行したか、実行段階の順序は記録へそのまま残し、構成から固定しません。Rootの利用者による推論レベル上書きは`provenance.root_override`とRootの実行段階の有効な`model` / `reasoning_effort`へ記録します。Rootモデルは比較条件のマニフェスト指定（Astra）から変更せず、Lunaワーカーと独立レビューのモデル/推論レベルも固定します。一つの記録内ではRootモデル/推論レベル条件を再試行の間でも変えず、変更する場合は別の実行/記録として扱います。集計は有効なRootモデル/推論レベルごとにグループを分け、異なるRoot推論レベル条件を混ぜません。

## パイロットとホールドアウト

まず各条件でパイロットを実行し、タスクの説明、受け入れ条件の評価基準、新しいセッション、権限/モデルの観測、使用量の取得が機能するか確認します。パイロット結果で条件を選ばず、手順を固定してから未使用のホールドアウトを同じ条件へ割り当てます。各タスクはクリーンなチェックアウトと新しいセッションで開始します。

`observed_model_run`には一意の`run_id`、セッション参照、完全な対象リビジョン、Codexバージョン、設定/プロンプト/Skill一式のダイジェスト、新しいコンテキストのフラグ、実際のモデルと権限の証拠を記録します。設定解析だけでは有効なモデル、再ルーティング、権限、新しいコンテキストの証拠になりません。`manual_record`は手入力の記録、`synthetic_fixture`はスキーマ/集計用の合成記録として、観測された実行と明確に分けます。

同じ受け入れ条件の評価基準を、実行者とは別の外部評価者またはブラインドレビュアーが判定します。`grade_refs`は再現可能なテスト、差分、評価成果物を指し、`acceptance_evidence`はマニフェストの受け入れ条件と同じ順序で記録します。`accepted`は全評価条件の結果と一致させます。合成フィクスチャの文字列は実モデルの品質を示しません。

## 記録とタスク全体の集計

JSONLの1行が1つのタスク/評価構成結果です。既存の`task_id`、`strategy`、`split`、`accepted`、`task_input`、`acceptance_evidence`、`provenance`を保持し、`grade_refs`を追加します。`attempts`は1から連番で、各試行は評価基準の結果としての`accepted`、`wall_time_seconds`、`wall_time_source`、実行した`stages`を持ちます。`accepted=false`は、失敗した実行段階と`failure_evidence`を伴う実行エラー、または全実行段階が`completed`でも評価基準を満たさない品質失敗のどちらも記録できます。再試行前の品質失敗も分母から除かず、最終試行の結果は記録の`accepted`と一致させます。

各実行段階は`sequence`、一意の`invocation_id`、`role`（`root` / `worker` / `review`）、有効な`model` / `reasoning_effort`、`status`、`failure_evidence`、`usage`、`elapsed_seconds`、再現可能な`evidence_refs`を持ちます。`sequence`は記録された実行順を表します。Astra-onlyのワーカーは拒否されますが、Astra+Lunaのワーカー数は0以上です。タスクの`review_required`が`true`なら最終試行へレビューを含めます。並列実行や再試行の数をこのバリデーターが知らないため、実際の全呼び出しを記録する責任は実行担当/Rootに残ります。

`usage`は`tokens`、ホストから得た`codex_usage`、`api_cost_usd`、各`source`を分けます。欠測は`null`とし、欠測を0へ変換しません。観測したCodex使用量とAPI費用の米ドル推定値・アカウントから報告された費用は別集計で、API価格表、価格DB、費用推定を行う実行機構は持ちません。必要な使用量が一つでも不明なら該当合計は`unknown`で、単純な費用差から費用削減を主張しません。実経過時間も実行段階合計ではなく試行単位で記録します。
出典は証拠種別と一致させます。`synthetic_fixture`は`synthetic`/`unknown`、`manual_record`は`manual`/`unknown`、`observed_model_run`は使用量と実経過時間が`observed`/`unknown`、費用が`account_reported`/`api_estimate`/`unknown`です。これにより、合成値が観測された使用量やアカウントから報告された費用として集計されません。

~~~bash
python3 scripts/model_selection_eval.py --plan
python3 scripts/model_selection_eval.py --validate-results /path/to/results.jsonl
python3 scripts/model_selection_eval.py --summarize /path/to/results.jsonl
~~~

集計は有効なRootモデル/推論レベルごとにグループを分け、タスク分母を保った合格件数（`accepted`）、試行/実行段階/レビュー/ワーカー件数、出典付きトークン、Codex使用量、API費用、実経過時間を出します。統計的優越、非劣性、費用削減を自動で主張しません。パイロット/ホールドアウトの比較には、同じ検証、外部評価、実際の権限/モデル/新しいコンテキストのイベント、全再試行/失敗、適切なホスト使用量の証拠が必要です。

`scripts/validation/fixtures/model_eval_offline.jsonl`は`synthetic`と明記したパイロットフィクスチャです。直接実装・レビューなし、状況に応じた委譲でワーカーなし、重大なリスクのレビュー、複数ワーカー、再試行前の品質失敗、最終品質失敗、誤った役割・モデル・実行順、呼び出しの重複、失敗の証拠の欠落、Root推論レベル上書き、実行を観測した記録の来歴のバリデーター経路を確認します。これは実際のベンチマークではなく、品質、ホスト割り当て上限、API費用、費用削減の証拠ではありません。
