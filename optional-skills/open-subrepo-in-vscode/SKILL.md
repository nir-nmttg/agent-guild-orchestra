---
name: open-subrepo-in-vscode
description: "明示された一つのworkspace directoryをVS Codeの新規windowで開く要求を、exact planと承認済みhelperで安全に扱います。"
metadata:
  owner: agent-guild-orchestra
  scope: optional-vscode-launch
---

# open-subrepo-in-vscode

既存の固定Guild構造に依存せず、ユーザーが明示した一つのabsolute directoryをVS Codeの新規windowへ開く要求だけを扱います。これは明示 invocation専用です。folder内のrepo操作、workspace設定、拡張機能、Git、build、testは行いません。

## 使う時

- ユーザーが特定のabsolute directoryをVS Codeで新規windowに開くよう明示した時
- Rootがtarget directory、起動目的、read-only表示範囲を固定できる時

## 使わない時

- directory、launcher、目的、承認範囲が曖昧な時
- 個別ファイル、任意アプリ、既存window、workspace設定、extension、repo操作を開くことが目的の時
- `code`の実体検証、runtime approval、visual confirmationができない時

## 手順

1. 同梱`open_directory_in_vscode.py --plan --directory <absolute-directory>`で、targetの実path、信頼できるVS Code launcher、正確な`-n` argv、`plan_id`を作る。planはsubprocessを起動しない。
2. planのtargetとlauncherを人間へ表示し、runtimeのGUI escalation/approvalを取得する。approvalがない、拒否された、targetやlauncherが変わった場合は止める。
3. 同じexplicit directoryに`--execute --approved-plan-id <plan_id>`を渡す。helperは実行直前に再計画し、identityが一致しない時は起動しない。shell interpolationや`open -a`に切り替えない。
4. exit 0はlaunch request acceptedだけを示す。VS Codeが視覚的に開いたとは、別の人間観測なしに報告しない。

## 出力

- canonical target directory、launcher identity、argv、plan_id（承認用plan）
- status、launch state、exit code、visual confirmation
- approval不足、target/launcher不一致、launcher不在、実行失敗の理由

## 安全

- exact target directory以外を読み書きせず、directoryから別のrepoやpathを再特定しない。
- symlink、relative path、missing directory、固定bundleと一致しないlauncherを拒否する。
- secrets、credentials、tokens、passwords、keys、auth data、PII、file contentsを読まず、表示せず、記録しない。
- helperのexit 0をvisual successへ言い換えない。runtimeのsandbox escalationと人間承認を迂回しない。

## 停止条件

- launch request acceptedをvisual confirmation unknownのまま正確に報告できた時
- 入力不正、approval不足、launcher不在、identity mismatch、nonzero exit、scope拡張を検出した時
