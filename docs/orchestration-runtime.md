# v3 orchestration

Agent Guild Orchestra 3はCodexのtemplate distributionです。独自のqueue、scheduler、database、inbox、status machineは動かしません。会話履歴とCodexのnative subagent / message機能が作業状態です。

## Root

RootはAstra / highをdefaultにします。利用者がsessionで別のsupported reasoning effortを指定した時は、その選択を尊重します。Rootは調査、編集、検証を直接行えます。

委譲は作業量を増やすためではなく、独立して進められるsubtaskに使います。小さな修正や一続きの作業はRootが終えます。分離可能な実装が十分大きい場合、RootはAdventurerへobjective、success criteria、path scope、許可操作、必要な検証を渡します。共有fileを複数writerへ割り当てません。

## Adventurer

AdventurerはLuna / maxのbounded implementation workerです。割り当てられたpathと操作の中で実装し、関連するcheckを実行し、変更file、検証結果、残る問題をRootへ返します。追加agentは起動しません。

## Inquisitor

InquisitorはAstra / highのread-only reviewerです。security、installer、local Git helper、migration、公開互換性など、material riskを伴う変更でRootが独立確認を必要とする時に使います。routine check failureを修正して同じcheckが通っただけなら、必ずreviewを追加する規則はありません。

Inquisitorは実装を変更せず、観測したfile / line / command結果へfindingを結び、blockingかnonblockingかをRootが判断できる形で返します。実装者とreviewerを同じagentにしないことで、重要な仮定を独立に検査します。

## Artifactとcheckpoint

boundary_guardはtask contract、assignment、result、review receipt、checkpointを検証できます。これらは必要な場合だけ使うJSON artifactであり、常駐workflowの状態ではありません。通常の作業はnative historyとmessageで足ります。

長い作業のcheckpointにはtarget root、scope、現在のsnapshot、完了した検証、未解決事項、次の行動だけを入れます。raw transcript、secret、credential、個人情報を保存しません。

## Gitと外部操作

snapshot_digestが実Git状態からsnapshotを生成します。git_guardはexplicit operationとscope、事前snapshotを照合し、限定されたlocal Git操作の後に新しいsnapshotを返します。これらは対象取り違えとstale evidenceを検出します。caller identityや権限を証明するACLではありません。

rebase、hard reset、clean、force updateなど復旧が難しい操作は通常の安全確認に従います。push、Pull Request、comment、deployなど外部状態を変える操作は、送信targetと内容を確認してから実行します。

## 並行作業

Codex設定はagents.enabledを有効にし、primary sessionを除く同時subagent数を2に制限します。この値はtoken / costの上限ではありません。Rootは依存関係のない作業だけを並行化し、writerの結果を統合してから全体検証を行います。
