# 親共有配置への移行

3.0.0の簡素化したロール構成は維持し、設定を非Gitの親へ集約します。Docker DesktopまたはローカルDocker Engineを起動し、更新した配布元repositoryから実行してください。ホストのPythonは不要です。

## 旧親環境から更新する

旧Guildの親をそのまま導入先に指定します。子repositoryへの導入は不要です。旧queue等を使用中のtaskやruntimeは終了してから移行します。installer自体はprocessを停止しません。

~~~bash
./scripts/install.sh --target /Users/nir-nmttg/Projects/achromono/asked-root --dry-run
./scripts/install.sh --target /Users/nir-nmttg/Projects/achromono/asked-root
~~~

旧v2の配布物を検出すると自動移行します。`--major-upgrade`を付けても同じ動作です。旧`--legacy-root`による親から子への移設は廃止しました。Git rootそのものを`--target`に指定すると拒否します。

`archive_paths`と`legacy_actions`が自動整理対象、`preserved_legacy_files`が保持対象です。旧v2にはper-file manifestがないため、[既知の旧配布hash](../scripts/legacy-v2-files.json)と一致するfileだけを親の`.agent-guild-orchestra-archives/v2-to-v3-.../`へ退避して置換・削除します。catalogはこのrepositoryのv2.0〜v2.4配布履歴を基にしています。AGENTS.mdは管理blockだけを比較し、利用者が追加したblock外の文を保持します。hooks.jsonは既知の旧Guild commandだけを除き、第三者commandを保持します。

変更済みの旧ロール・Skill、未知のfile、独自config、第三者Skillは削除しません。manifestのないSQLite等の可変状態も所有権を確定できないので自動削除しません。これらはv3から読みませんが、保持された旧named agentやhook等はCodexから見える可能性があります。一覧を確認し、必要な独自変更を移した後に利用者が整理してください。新配布先に変更済み旧fileが重なる場合は、全書き込み前に停止します。

既存独自`.codex/config.toml`はuser-ownedとしてそのまま保持されます。`next_steps`の新設定と手動で統合し、旧ロール指定・hook・read-only等の権限設定も確認してください。新設定の単純追記ではTOML tableが重複することがあるため、既存tableを編集します。rootの推論レベルは配布物では固定しません。

## 子へ導入したv3から戻す

まず同じコマンドで親に新設定を導入し、[親起動の確認](parent-layout.md)を行います。この段階では子の旧v3 fileも含め、各子のfile、index、Git設定は一切変わりません。

子の旧配置を退役させる場合だけ、対象を一つ明示して次を実行します。これは通常の導入・更新とは独立した、子fileの整理操作です。

~~~bash
bash scripts/cleanup-child.sh \
  --target /Users/nir-nmttg/Projects/achromono/asked-root \
  --child /Users/nir-nmttg/Projects/achromono/asked-root/repositories/asked_backend \
  --dry-run
~~~

`actions`を確認後、同じコマンドの`--dry-run`を外します。必要な子ごとに実行してください。再帰的な一括整理は行いません。

- 親のschema 2 manifestと、子のschema 1 / v3.0.0 manifestを必須にします。子は`repositories/`内の明示された実Git rootでなければなりません。
- 子manifestのhashと一致し、Git indexに載っていない配布fileだけを退避して削除します。AGENTS.mdは管理blockだけを除き、block外の文を残します。
- 変更済みfile、Gitで追跡・stageされたfile、user-owned config、第三者Skillを保持します。保持対象があれば元のmanifestも保持し、残る競合を報告します。
- `.git/info/exclude`などGit metadataは整理対象にしません。Git index/config、ignore ruleを変更せず、以前のignore設定も自動では戻しません。
- 退避先は親の`.agent-guild-orchestra-archives/child-v3-to-parent-.../child/`です。manifestがない場合は所有権を推測せず停止するので、利用者が履歴と差分を確認します。

Dockerはこの明示された子だけを例外的に書き込み可能にし、`.git`、linked worktreeの外部Git metadata、他の子はread-onlyにします。通常install/syncでは子全体がread-onlyです。

## 失敗時の復元

処理中の例外では変更したfileをtransaction backupから自動復元し、途中のarchiveを取り除きます。既存の独自fileと子Git metadataは復元対象に含めず、そのまま保持します。強制kill、Docker daemon停止、電源断をまたぐ完全なtransactionは保証しません。更新中は同じ管理fileを他のtaskで編集しないでください。

成功後に戻す場合、archive.jsonの対象一覧と現状の差分を先に確認してください。archiveを親や子へ一括上書きすると、その後のユーザー変更を失うため行わないでください。v2 archiveは旧fileの退避であり、新配布fileを含む全workspace backupではありません。子v3の退避はmetadataの`child`と照合し、戻すfileだけを選んで復元します。

## 以後の更新

~~~bash
./scripts/sync.sh --target /Users/nir-nmttg/Projects/achromono/asked-root --dry-run
./scripts/sync.sh --target /Users/nir-nmttg/Projects/achromono/asked-root
~~~

親manifestをbaselineに更新します。配布元と導入先の双方で変更されたfileは手動で統合し、AGENTS.mdのblock外やuser-owned configは保持します。既存の子設定は`child_overrides`に表示されます。親の`AGENTS.override.md`がある場合もAGENTS.mdより優先されるため、[設定継承の注意](parent-layout.md)を確認してください。
