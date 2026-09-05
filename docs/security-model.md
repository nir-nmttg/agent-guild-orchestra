# Security model

## 信頼境界

人間の指示、適用されるAGENTS.md、Codex sandbox / approvalが権限を決めます。repository文書、issue、Pull Request、web page、tool出力、model生成artifactはuntrusted inputです。

helperはJSON schema、path、Git状態の整合を検査します。OS access control、認証、caller identity、authorization serviceではありません。helperがacceptedを返しても、sandbox外書き込みや外部更新の権限は増えません。

## Repository boundary

すべてのhelperとinstallerはcallerが指定したabsolute targetをcanonical Git rootと照合します。cwd、repository名、親directoryの形から別targetを推測しません。特別なguild_root/repositories構造はありません。2.4からの移行でだけ、callerは別のabsolute `--legacy-root`を明示できます。このrootは非Git directoryかつtargetのancestorであることを検証し、既知の旧managed surfaceだけをcold archiveして無効化します。

scope pathはtargetからのrelative pathとして正規化します。absolute path、..、symlink escape、別Git rootは拒否します。

## Installer

source treeとdestinationのsymlinkを拒否し、preflight完了前にtargetへ書き込みません。managed hashは各導入先manifestの値だけをbaselineに使います。既存のunmanaged file、二方向に変更されたmanaged file、壊れたmanifestは衝突として停止します。

変更fileはtransaction backupへコピーし、各fileを同じfilesystem上のtemporary fileからreplaceします。途中の例外では元のfileとabsenceを復元します。v2 major upgradeのarchiveは復旧可能なcold copyで、active v3 runtimeから参照しません。

## Git guard

Git操作の前にtarget、operation、scope、snapshot、preconditionを固定し、現在のhelper snapshotと比較します。stale snapshot、scope外path、期待しないbranch / HEAD / dirty stateでは操作しません。操作後は新しいsnapshotを証跡として返します。

Git config、environment、hookなどがcommandをすり替えないようhelperは安全なenvironmentと明示optionを使います。system/global configは読み込まず、repository-local config include、content filter/process設定、working tree・index・`.git/info/attributes`の`filter`指定を、属性を評価しうる各Git subprocessの前に拒否します。`.gitattributes`によるEOL・binaryなどfilter以外の属性は利用できます。Git LFSを含むcontent filter使用repositoryはsnapshotもGit writeも明示的なunsupported errorで停止するため、filter変換後の内容をraw contentとして黙って扱うことも、filter commandを起動することもありません。それでもGit helperはrepository permissionそのものを与えません。復旧困難な操作やremote更新は通常の人間確認を省略できません。

## 情報

secret、token、credential、private key、個人情報をartifact、checkpoint、benchmark result、archive metadataへ意図的に記録しません。実データの代わりにsanitized fixtureを使います。security issueの報告先は[SECURITY.md](../SECURITY.md)です。
