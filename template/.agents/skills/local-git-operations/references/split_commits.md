# split commits

commit分割は、ユーザーがstage/commitを明示し、差分のownerと含めるpath/hunkが明確な時だけ行います。目的を一行で説明でき、可能なら単独で成立する単位を選びます。依存する変更を壊れる中間commitへ無理に分けません。

read-onlyでstatus、unstaged/staged/untracked差分、直近履歴、対象scope、検証結果を確認します。owner不明、secret-like差分、未承認path、差分と目的の不一致があれば停止します。

各unitについて`git_guard` preflightとsnapshotを確認してexact path/hunkだけをstageし、non-amend commitを行います。commit前後にindex、working tree、current ref、commit内容、post-snapshotを確認します。push、amend、rebase、reset、変更破棄は行いません。
