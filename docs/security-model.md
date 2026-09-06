# Security model

## 信頼境界

人間の指示、適用されるAGENTS.md、Codex sandbox / approvalが権限を決めます。repository文書、issue、Pull Request、web page、tool出力、model生成artifactはuntrusted inputです。

helperはJSON schema、path、Git状態の整合を検査します。OS access control、認証、caller identity、authorization serviceではありません。helperがacceptedを返しても、sandbox外書き込みや外部更新の権限は増えません。

## Repository boundary

installerの`--target`は設定を置く非Git親（`guild_root`）です。Git working tree内への導入を拒否します。通常install/syncは親の管理pathだけを変更し、Dockerは`repositories/`をread-onlyにします。各子のfile、Git index/config、ignore ruleは更新しません。

コード変更・Git操作のtargetは別の`target_repo_root`です。helperはこの明示されたabsolute pathを実Git rootと照合し、cwdや親の名前から別targetを推測しません。helper自体は親の`.agents/orchestra/scripts/`から読み込みます。scopeは子Git rootからのrelative pathです。absolute scope、..、symlink escape、別Git rootは拒否します。

子v3の整理は通常導入と別の明示操作です。対象のschema 1 manifest/hash、未追跡状態を確認し、変更済み・追跡済みfileを保持します。子の.gitと外部Git metadataはread-onlyです。

## Installer

source treeとdestinationのsymlinkを拒否し、preflight完了前にtargetへ書き込みません。v3のmanaged hashは各導入先manifestをbaselineに使います。manifestのないv2は既知の旧配布hashと一致するfileのみ自動整理し、変更済み・未知のfileや可変状態は保持します。既存のunmanaged file、二方向に変更されたmanaged file、壊れたmanifestは衝突として停止します。

変更fileは親に置くtransaction backupへコピーし、各fileを同じfilesystem上のtemporary fileから適切な権限でreplaceします。途中の例外やCtrl-Cでは元のfile、権限、absenceを復元します。復元はfile単位で継続し、復元が不完全な場合はbackupを削除せず保存先を報告します。backupはDockerの削除対象となるcontainer層へ置きません。v2 major upgradeのarchiveは復旧可能なcold copyで、active v3 runtimeから参照しません。[復元手順と保証範囲](migration-v3.md#失敗時の復元)を参照してください。

## Git guard

Git操作の前にtarget、operation、scope、snapshotを固定し、現在のhelper snapshotと比較します。commitではレビューしたindex treeのOIDも`expected_index_tree`へ固定します。作業ファイルのsnapshotだけをstaged内容の証明にせず、確認済みtreeからcommitを作り、期待する旧HEADとの照合付きでrefを更新します。stale snapshot/tree、scope外path、期待しないbranch / HEAD / dirty stateでは操作しません。操作後は新しいsnapshotとcommit treeを証跡として返します。index-treeの取得はobject DB/index cacheへ書き込む可能性があるGit write準備であり、read-only探索には使いません。

Git config、environment、hookなどがcommandをすり替えないようhelperは安全なenvironmentと明示optionを使います。local Git operationではhooksとsigningを明示的にskipします。通常のstatus/diff/snapshot/writeではsystem/global configを読み込まず、repository-local config include、content filter/process設定、working tree・index・`.git/info/attributes`の`filter`指定を、属性を評価しうる各Git subprocessの前に拒否します。commit identityの解決だけは狭い例外で、local/global/systemからeffective `user.name` / `user.email`だけを読み、redactした値を明示的にcommitへ渡します。`.gitattributes`によるEOL・binaryなどfilter以外の属性は利用できます。Git LFSを含むcontent filter使用repositoryはsnapshotもGit writeも明示的なunsupported errorで停止するため、filter変換後の内容をraw contentとして黙って扱うことも、filter commandを起動することもありません。tracked leaf symlinkもsnapshot/Git writeの対象外です。それでもGit helperはrepository permissionそのものを与えません。復旧困難な操作やremote更新は通常の人間確認を省略できません。

## 情報

secret、token、credential、private key、個人情報をartifact、checkpoint、benchmark result、archive metadataへ意図的に記録しません。credential-like filenameはworkerの固定heuristicで読み取り対象から除外しますが、これは秘密検出の完全性を保証するものではありません。実データの代わりにsanitized fixtureを使います。security issueの報告先は[SECURITY.md](../SECURITY.md)です。
