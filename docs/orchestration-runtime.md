# v3 orchestration

Agent Guild Orchestra 3はCodexのproject-local configuration、二つのcustom agent、core Skill、stateless Git/snapshot helperを配布します。会話履歴とCodex native task/messageが作業状態です。独自queue、scheduler、database、inbox、status machineはありません。

## 起動場所と作業対象

設定は非Git親`guild_root`に一組だけ置き、その親をCodexでtrustして新しいlocal taskを開始します。子Git repositoryは`repositories/`に置きます。親の設定・Skill探索を維持するためsessionの基点を親に保ち、コード変更commandは明示workdir、Git commandは実Git rootを指定します。各childとnested pathのAGENTS指示を先に読み、子設定との競合を報告します。設定探索の実測と限界は[親配置](parent-layout.md)を参照してください。

委譲には`guild_root`と対象の`target_repo_root`を渡します。helperは前者から読み込み、snapshot/guardは後者を検証します。複数repositoryを扱う場合、Git操作の対象と承認・snapshotをrepositoryごとに分けます。インストールと更新のDocker containerは終了時に削除され、常駐runtimeにはなりません。

## Rootとdelegation

RootはAstra modelを使い、project configでreasoning effortを固定せず、user-selected supported effortを尊重します。小さな一続きの作業はRootが直接終え、十分に独立したbounded scopeだけをAdventurerへ渡します。AdventurerはLuna / maxのworkerで、追加agentやcross-scope integrationを行いません。

InquisitorはAstra / xhighのfresh independent read-only reviewerです。security、installer/runtime contract、Git/external publication、breaking compatibility、migration、広いblast radius、important unresolved questionのmaterial triggerでだけ使います。routine check failureを修復して再実行できたことだけでは起動しません。reviewが不要なlow-risk taskは直接完了できます。

Rootはintegration前にtarget、全initial status/diff、planned writer unionを記録します。worker結果を統合した後、そのunionと実際の変更を照合し、pre-existing user editを保持します。union外の変更や帰属不明の変更を見つけたら停止して報告します。別writerの変更を自動revertしたり、attribution engineを作ったりしません。

## Native handoffとresult

handoffはpurpose、objective、acceptance criteria、target、owned scope、authorityだけを短く渡します。workerのresultはchanges、tests、unresolved issues、unrun checksと理由、snapshot evidence（取得した場合）を返します。大きなassignment/result/review schemaや常駐artifact workflowを追加しません。

Codexのworker上限は設定値であり、tokenやcostの上限ではありません。完了したworkerをcloseできるnative lifecycleが公開されている場合はreview前に閉じます。close機能がhostにない場合、空きslot不足を正確に報告し、cleanup serviceを発明しません。

## Gitとsnapshot

Git writeまたは明示的なstale-risk確認の時だけ`snapshot_digest`を使います。探索だけでsnapshotを作りません。`git_guard`はexact operation、target、scope、precondition、pre-snapshotを照合し、限定されたlocal Git write後にpost-snapshotを返します。両helperはcaller identityやrepository permissionを証明しません。

content-unchanged stageは追加のmodel reviewを発生させません。hooksとsigningはhelperが明示的にskipします。Git LFS/content-filter repository、content filter/process設定、tracked leaf symlinkはsnapshot/Git writeのunsupported境界として停止します。credential-like filenameはworkerの固定heuristicで読み取り対象から除外し、結果へ内容を記録しません。

通常の作業はnative historyで足ります。明示的な中断再開が必要な時だけ、target、scope、現在のsnapshot、完了check、unresolved issue、次のactionを含むsanitized checkpointを使います。
