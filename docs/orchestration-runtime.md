# v3 orchestration

Agent Guild Orchestra 3はCodexのproject-local configuration、二つのcustom agent、core Skill、stateless Git/snapshot helperを配布します。会話履歴とCodex native task/messageが作業状態です。独自queue、scheduler、database、inbox、status machineはありません。

## Rootとdelegation

RootはAstra / highをdefaultにし、user-selected supported effortを尊重します。小さな一続きの作業はRootが直接終え、十分に独立したbounded scopeだけをAdventurerへ渡します。AdventurerはLuna / maxのworkerで、追加agentやcross-scope integrationを行いません。

InquisitorはAstra / highのfresh independent read-only reviewerです。security、installer/runtime contract、Git/external publication、breaking compatibility、migration、広いblast radius、important unresolved questionのmaterial triggerでだけ使います。routine check failureを修復して再実行できたことだけでは起動しません。reviewが不要なlow-risk taskは直接完了できます。

Rootはintegration前にtarget、全initial status/diff、planned writer unionを記録します。worker結果を統合した後、そのunionと実際の変更を照合し、pre-existing user editを保持します。union外の変更や帰属不明の変更を見つけたら停止して報告します。別writerの変更を自動revertしたり、attribution engineを作ったりしません。

## Native handoffとresult

handoffはpurpose、objective、acceptance criteria、target、owned scope、authorityだけを短く渡します。workerのresultはchanges、tests、unresolved issues、unrun checksと理由、snapshot evidence（取得した場合）を返します。大きなassignment/result/review schemaや常駐artifact workflowを追加しません。

Codexのworker上限は設定値であり、tokenやcostの上限ではありません。完了したworkerをcloseできるnative lifecycleが公開されている場合はreview前に閉じます。close機能がhostにない場合、空きslot不足を正確に報告し、cleanup serviceを発明しません。

## Gitとsnapshot

Git writeまたは明示的なstale-risk確認の時だけ`snapshot_digest`を使います。探索だけでsnapshotを作りません。`git_guard`はexact operation、target、scope、precondition、pre-snapshotを照合し、限定されたlocal Git write後にpost-snapshotを返します。両helperはcaller identityやrepository permissionを証明しません。

content-unchanged stageは追加のmodel reviewを発生させません。hooksとsigningはhelperが明示的にskipします。Git LFS/content-filter repository、content filter/process設定、tracked leaf symlinkはsnapshot/Git writeのunsupported境界として停止します。credential-like filenameはworkerの固定heuristicで読み取り対象から除外し、結果へ内容を記録しません。

通常の作業はnative historyで足ります。明示的な中断再開が必要な時だけ、target、scope、現在のsnapshot、完了check、unresolved issue、次のactionを含むsanitized checkpointを使います。
