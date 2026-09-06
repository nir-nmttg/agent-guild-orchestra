# git_guard reference

`git_guard`は機械的な境界確認を提供します。この配布に対応する呼び出し方は以下のとおりです。interfaceが異なる場合は`--help`を確認し、未対応なら停止します。通常の利用のためにhelper全文や配布されていない開発testを読み直す必要はありません。

## Current template interface

このtemplateのruntimeでは、まずsnapshotをCLIまたは同じmoduleの`compute_snapshot(...)`で発行します。

helperの配置場所は非Git親の`<guild_root>/.agents/orchestra/scripts/`です。以下のCLIはその絶対pathで呼び出し、`--repo`とcontractの`target_repo_root`には操作する子の実Git rootを渡します。親にGit操作を向けたり、子にhelperを複製したりしません。

```text
snapshot_digest.py --repo <absolute-git-root> --kind revision_only|working_tree_content|commit_range [--base-ref <ref>] [--head-ref <ref>] [--scope <relative-path>]... [--untracked <relative-path>]...
```

`working_tree_content`には一つ以上の`--scope`が必要で、そのscope内の実untracked pathをすべて`--untracked`へ列挙します。`commit_range`には`--base-ref`と`--head-ref`が必要です。返ったcanonical mapping全体をevidenceとして保持し、`snapshot_id`や`diff_hash`だけを作成・置換しません。

通常のhandoff、result、review、checkpointはCodex native task historyへ短く記録します。runtimeにはassignment/result/review artifactを検証する常駐schema validatorはありません。`git_guard`へ渡すassignment JSONは、明示されたGit operationのcontractとしてだけ扱います。

Git writeは、closed allowlistのoperationを一つだけ選び、assignment JSON fileを渡します。stageでpatchを使う場合だけ`--patch-file`または`--patch`を追加します。

```text
git_guard.py apply --operation <allowed-operation> --contract <json-file> [--patch-file <patch-file>]
```

contractには`target_repo_root`、`allowed_operations`、`path_or_ref_scope`、helper出力の`subject_snapshot`を渡します。commitだけはscope内の`message`と、確認済みの`expected_index_tree`も必要です。assignmentのtype/id、空のpostconditions、自己申告のprecondition booleanは不要です。未対応のflagや互換fallbackを推測しません。

Current closed allowlistは`branch_create_and_switch_new`、`rename_origin_unpushed_branch`、`stage_exact_paths_or_hunks`、`unstage_index_only_exact_paths`、`commit_non_amend`です。`git_guard.py`は成功時に`preflight_snapshot`、`postwrite_snapshot`、concise operation evidenceを返し、patch本文やcommit messageを返却しません。

## preflight

- ユーザーまたはmainが明示したGit rootと、実際のGit rootが一致することを確認する。
- operation、current/base/new ref、exact path/hunk、untrackedの扱い、許可された副作用をassignmentへ固定する。
- detached HEAD、protected ref、merge/rebase/cherry-pick途中、既存変更のowner不明、対象外path、secret-like差分を検出したら止める。
- runtime snapshot helperを同じtarget、kind、base/ref、scopeで使い、assignmentのpre-snapshotと一致するevidenceだけを採用する。不一致は`stale_evidence`として扱い、digestを手計算しない。

## write boundary

許可するlocal操作は、今回明示されたものに限ります。通常の閉じた集合はnew branch create/switch、origin未push確認済みbranch rename、exact path/hunkのstage、indexだけのexact-path unstage、non-amend commitです。既存branchへの一般switchや、変更を破棄する操作は含めません。

`git_guard`、sandbox、approval、ユーザー指示が別々に示す権限を足し合わせてscopeを広げません。helperが拒否したら、別コマンドや別pathへ迂回せず停止します。

`working_tree_content` snapshotはworktree contentとscopeを結び付けます。commit準備では次のCLI、または`git_guard.index_tree(target_repo_root)`からindexのtree OIDを取得します。

```text
git_guard.py index-tree --repo <absolute-git-root>
```

これは`git write-tree`によるGit writeの準備です。worktree内容を変更しませんが、object DBやindex cacheへ書き込むことがあるため、純粋なread-only探索では実行しません。返った`expected_index_tree`とsnapshotの`revision_id`の差分を、helperの安全なGit実行経路（`snapshot_digest.run_git`で`diff --no-ext-diff --no-textconv --ignore-submodules=none <revision_id> <expected_index_tree> -- <owned paths>`）で確認します。変わり得る現在indexだけを見て、別treeを承認したことにしません。

確認したOIDをcontractの`expected_index_tree`へそのまま渡します。guardはindex照合後もその固定treeからcommitを作り、期待する旧HEADを照合してrefを更新します。renameは両endpointをscopeへ含めます。競合で失敗した場合は状況を読み直し、未確認treeへの差し替えや自動rollbackをしません。

## postflight

write後はcurrent ref、status、意図したpath/index/commit状態、外部remoteが変更されていないことを確認します。snapshot helperを別のpostcondition evidenceとして発行し、preと同一digestでも実行回数の証明とは扱いません。変更が想定外なら追加writeをせず、mainへ返します。

## forbidden

push、PR、Issue、comment、merge、release、deploy、reset、restore、clean、amend、rebase/filter、branch/ref/tag delete or force move、破壊的stash、secret/PIIの読み書きはこのreferenceで許可されません。
