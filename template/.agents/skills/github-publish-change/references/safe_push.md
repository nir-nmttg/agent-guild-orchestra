# safe push preflight

push前に、ユーザーまたはmainが指定したtarget repository、remote、head ref、base ref、commit rangeを固定します。current ref、detached/protected状態、working tree、upstream、remote-tracking head、remote head、既存PRをread-onlyで確認し、remote URLの機微な値は出力しません。

差分の目的、検証結果、公開してよいpathを確認します。secret、credential、token、password、key、PII、内部情報、未公開情報、意図しない生成物や巨大成果物を検出したら公開を止めます。確認できないものを安全と扱いません。

local Git writeが必要な場合は`local-git-operations`と`git_guard`へ戻ります。このreferenceはpushやPR作成を許可しません。
