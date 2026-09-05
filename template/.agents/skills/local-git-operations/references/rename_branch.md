# rename branch

branch renameは、ユーザーがrenameを明示し、現在branchがoriginへpushされていないことを確認できる時だけ行います。対象Git root、current ref、new ref、差分のowner、postconditionを固定します。

detached HEAD、protected ref、merge/rebase/cherry-pick途中、未コミット変更のowner不明、複数目的の混在、upstreamまたはremote-tracking refの存在、remote確認不能、既存PR、new ref衝突のいずれかがあれば停止します。remoteを変更するためのpush、delete、forceは行いません。

`git_guard` preflightとsnapshotが一致した時だけ現在refをrenameし、postflightでnew ref、status、差分保持、remote unchangedを確認します。
