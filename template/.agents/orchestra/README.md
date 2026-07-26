# orchestra runtime

`config/settings.yaml`は機械契約、`AGENTS.md`はモデル向けcompact kernel、`queue/templates`とSQLiteは実行状態の正本です。詳細なschemaをagent promptへ重ねません。

## 配置

- `config/settings.yaml`: safety、delegation、Trial、workerの機械設定
- `instructions/`: roleの補助資料。custom agentの起動promptには常時追加しない
- `queue/templates/`: compact assignment/reportの雛形
- `scripts/queue_db.py`: SQLite Ledger helper
- `scripts/snapshot_digest.py`: `agent-guild-orchestra-snapshot-v1` helper
- `<guild_root>/.orchestra/`: 動的状態

対象repoは `<guild_root>/repositories/<repo>` のGit rootである `target_repo_root`だけです。

## Lifecycle

1. 対象repoを読まない回答・説明はRootのfast pathで処理し、対象repoに触れる小さな調査・変更は追加のplanning ceremonyなしで適切なcustom agentへ直接割り当てる。
2. materialな作業はobjective、success criteria、scope、authority、validationを固定する。
3. Rootが必要なcustom agentを直接起動し、完了を待ってevidenceをgateし、次actionを決める。唯一のnested edgeである`inquisitor`→`examiner`以外は再委譲しない。
4. `adventurer`は単一bounded scopeを実装する。
5. 並列実装後の共有契約とglueは`artificer`がstable barrier上で統合する。
6. risk-triggeredな独立確認は`inquisitor`が行い、必要な単一focusだけ`examiner`へ渡す。
7. `courier`がLedger反映と、明示されたlocal Git操作だけを行う。

Rootはtarget・authority・snapshot・queueのcontrol-plane確認、routing、待機、evidence gate、最終synthesisに加え、roleが仕様化したbrowser-control toolだけを実行して観測事実を記録します。対象repoの探索、コード・差分の読み取り、実装、test、browserの計画/解釈、debug、review evidence収集は担当roleへ委譲します。Rootが`high`、`xhigh`、`ultra`のどのreasoning effortで起動しても、このnamed-role topologyは変わりません。

## Roles

- `cartographer`: read-only mapmaking
- `guildmaster`: 複数Partyの戦略
- `captain`: owned scope、順序、integration、Trial focusの設計
- `adventurer`: bounded実装
- `artificer`: cross-scope integration
- `inquisitor`: risk-based Trialと最終decision
- `examiner`: 単一focusのread-only evidence
- `sage`: 具体的な独立focusのread-only助言
- `warden`: 矛盾、反復失敗、scope driftなどの例外診断
- `courier`: Ledger反映と、Rootまたは人間が明示したlocal Git操作だけ

## Invariants

- target repo境界、secret/PII absolute deny、既存ユーザー変更を保持する。
- local Gitは具体的な人間指示、外部更新は実行直前の再確認を必須にする。
- snapshot mismatch、確認不能なlineage、authority不足はfail closedにする。
- 数値confidenceや定型的なskip/cost説明を成果判定に使わない。
- SQLite Ledgerには判断根拠、検証、snapshot、残リスクだけを残し、raw logや秘密値を残さない。
