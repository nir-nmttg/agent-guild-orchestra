# publish

公開前に、次の値を一つのexact scopeとして再確認します: target repository、remote、head/base ref、pushするcommit range、PR title/body、draft/ready、公開操作。ユーザーの以前のauthorizationを使う場合も、これらが同じであることを確認し、scope変更があれば再確認へ戻ります。

承認済みの範囲だけを一度実行します。force push、tags/all branches、remote追加、branch削除、merge、release、deploy、PR本文への推測追加は行いません。push成功後にPR作成が失敗しても、追加pushや削除で回復しません。外部結果、URL、ref、commit range、失敗理由を報告して停止します。
