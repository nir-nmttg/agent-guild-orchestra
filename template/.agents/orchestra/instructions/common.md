# 共通実行契約

`AGENTS.md` が全roleに適用される安全・権限・委譲の正本です。このファイルはrole文書を手動で参照する場合の補助であり、custom agentの起動時promptへ重ねて読み込みません。

Rootはtarget、authority、snapshot、queueを固定してassignmentを発行し、担当roleの完了を待ってevidenceをgateします。対象repoの探索、コード・差分・repo文書の読み取り、実装、validation、debug、review evidence収集はassignmentを受けたroleが行い、Rootへreportを返します。browser-control toolは例外としてRootだけが、roleが渡すobjective・URL・authority・許可操作どおりに実行して観測事実を記録します。roleはtoolを呼ばず、browser計画、許可操作の仕様化、観測根拠の解釈、reportを担います。

## 実行の基本

1. assignmentのobjective、success criteria、scope、authority、snapshotを確認する。
2. 既存設計とユーザー変更を読み、最小十分な方針を決める。
3. scope内で作業し、変更に直接対応する検証を行う。
4. evidence、未解決blocker、残リスク、snapshotをownerへ返す。

数値confidence、全状態の再記述、sage/examinerの未使用理由、固定回数の読み取り・検証は要求しません。正しさを塞ぐunknown、失敗したcheck、矛盾、scope drift、高リスクtriggerがある場合だけevidence stateを更新します。

## Fail closed

- target、authority、snapshot、owned scopeが確認できない
- secret・PIIを読む必要がある
- 未承認の破壊的操作、外部状態更新、依存追加、migration、deployが必要
- 既存ユーザー変更と自分の変更を区別できない

上記では変更を進めず、確認できた事実と必要な次の判断を返します。

## Handoff

handoffは次の核だけを必須にします。

- objectiveとsuccess criteria
- scopeとauthority
- 実施した変更または判断
- validation evidence
- helperが発行したsnapshot参照
- unresolved blockerとresidual risk

browserを使うhandoffには、objective、URL、authority、許可操作、Rootが記録した観測事実参照、roleの解釈を加えます。Rootはrepository workやbrowserの判断を代替しません。

metadata、lineage、digest、status enumはqueue/helperが生成・検証します。agentは値を推測しません。
