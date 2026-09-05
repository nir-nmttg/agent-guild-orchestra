---
name: open-subrepo-in-vscode
description: "明示された一つのworkspace directoryをVS Codeの新規windowで開く要求だけを扱います。"
metadata:
  owner: agent-guild-orchestra
  scope: optional-vscode-launch
---

# open-subrepo-in-vscode

ユーザーが明示した一つのabsolute directoryを、read-only表示目的でVS Codeの新規windowへ開きます。明示 invocation専用で、folder内のrepo操作や設定変更はしません。

## 使う時

- exact absolute directory、起動目的、read-only表示範囲が明示された時

## 使わない時

directory、launcher、目的、承認範囲が曖昧な時、個別file・既存window・workspace設定・extension・Git/build/testが目的の時、launcher identityやvisual confirmationを確認できない時。

## 手順

1. `scripts/open_directory_in_vscode.py --plan --directory <absolute-directory>`でcanonical target、trusted launcher、`-n` argv、plan_idを作る。planでは起動しない。
2. target、launcher、argvを表示してruntime GUI approvalを得る。approval、identity、targetが変われば止める。
3. 同じdirectoryへ`--execute --approved-plan-id <plan_id>`を渡す。helperが実行直前に再計画し、identity mismatchでは起動しない。
4. exit 0はlaunch request acceptedだけとして、visual confirmationなしに開いたとは報告しない。

## 出力

canonical target、launcher identity、argv、plan_id、status、launch state、exit code、visual confirmation、失敗理由。

## 安全

target以外を読み書きせず、symlink・relative path・missing directory・untrusted launcherを拒否します。secrets、credentials、tokens、passwords、keys、auth data、PII、file contentsを扱いません。shell interpolation、`open -a`、runtime approval迂回、外部アプリの別操作をしません。

## 停止条件

launch request acceptedをvisual confirmation unknownのまま正確に返せた時、または入力不正、approval不足、identity mismatch、launcher不在、nonzero exit、scope拡張が判明した時。
