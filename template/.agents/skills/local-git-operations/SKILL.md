---
name: local-git-operations
description: "明示された対象と範囲でbranch作成・未push branch rename・差分のcommit分割を扱うSkill。pushやPR公開は扱いません。"
metadata:
  owner: agent-guild-orchestra
  scope: local-git
---

# local-git-operations

ユーザーまたはRootが明示したlocal Git操作だけを、target、ref、path、ownerが確認できる範囲で行います。Git writeの前には[`git_guard`](references/git_guard.md)を読み、操作ごとのreferenceを一つだけ追加で読みます。

## 使う時

- branch作成・切替は[`create_branch`](references/create_branch.md)
- origin未pushと確認できるbranch renameは[`rename_branch`](references/rename_branch.md)
- 明示された差分のcommit分割は[`split_commits`](references/split_commits.md)
- 既存branch、差分、履歴のread-only確認

## 使わない時

push、Pull Request、merge、release、deployなどの公開は`github-publish-change`へ戻します。操作、target、ref、path、owner、承認範囲が不明な時は止めます。

## 手順

1. 設定を置く非Git親の`guild_root`と、操作対象の実Git root `target_repo_root`を分ける。helperは親の`.agents/orchestra/scripts/`から読み、Git引数には子の実Git rootを渡す。operation、current/base/new ref、path/hunk、許可された副作用を固定し、status、detached HEAD、保護branch、staged/unstaged/untracked、upstreamをread-onlyで確認する。
2. writeの時だけsnapshotを作り、`git_guard`へexplicit operation、scope、preconditionとともに渡す。explorationだけではsnapshotを作らない。
3. referenceのclosed allowlistだけを実行し、対象やscopeが変わったら止めて再契約する。
4. commit前は意図したstaged diffをread-onlyで確認する。`working_tree_content` snapshotはworktree contentとexact path scopeを示すが、staged hunk compositionは証明しない。write後にstatus、ref、対象差分、helperのpostcondition snapshotを確認し、この限界を含むpre/post evidenceと残るriskを返す。

## 出力

target、operation、ref/path scope、owner、pre/post snapshot、実行結果、未実行確認と理由、残るrisk。

## 安全

`git_guard`の拒否、snapshot mismatch、対象不一致、ownership不明を迂回しません。reset、restore、checkoutで変更を破棄する操作、clean、amend、rebase、ref削除・force move、破壊的stash、external updateはこのSkillで行いません。secrets、credentials、tokens、passwords、keys、auth data、PIIを扱いません。

## 停止条件

許可された操作とpre/post evidenceが完了した時、またはtarget、owner、snapshot、ref/path、承認、allowlistのいずれかを確認できない時。
