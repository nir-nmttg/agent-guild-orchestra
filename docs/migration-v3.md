# 2.4以前から3.0への移行

3.0.0はbreaking major releaseです。旧runtimeの状態を新runtimeへimportまたはreplayしません。

## 旧Guild rootから実repositoryへ移す

2.4の標準配置は、非GitのGuild rootに`AGENTS.md`、`.agents/`、`.codex/`、`.orchestra/`があり、その`repositories/`以下に実repositoryがある形でした。3.0は各実Git rootへ導入するため、旧rootと新targetの二つを明示します。installerはancestorを探索しません。

~~~bash
make validate
./scripts/install.sh \
  --target /absolute/path/to/old-guild-root/repositories/app \
  --config-mode managed \
  --dry-run
~~~

旧rootの`repositories/`配下にある各dependent childを一つずつ対象にし、まず通常の`--dry-run`、出力確認、通常installを完了させます。installerは兄弟repositoryを再帰発見せず、複数repositoryをatomicには更新しません。各childの導入後にCodexでtargetをtrustし、新しいtaskからeffective config、Rootのmodel/effort、`adventurer` / `inquisitor`のnamed-agent discoveryを確認します。installerの成功だけではactivationを確認したことになりません。

`.codex/config.toml`の所有権は`--config-mode managed`または`--config-mode user-owned`で明示します。user-ownedでは既存configのbytes（未認識のswitchを含む）を変更せず、manifestへmodeを保存します。次回更新は保存済みmodeを継承し、modeを明示的に切り替えた時だけ所有権を変更します。切替時も自動mergeはせず、managedからuser-ownedならconfigを保持し、user-ownedからmanagedなら配布configとの衝突を通常のupdate規則で確認します。

全childの通常導入、activation確認、manifest確認を終えた後、最後に一つの既に導入済みchildから、旧parent cleanupを含むmajor migrationを明示的に実行します。これは最後のchildだけを移行する手順ではなく、旧rootの共有managed surfaceをarchiveして除去する最終操作です。実行前に全childが完了していることを確認します。

~~~bash
./scripts/install.sh \
  --target /absolute/path/to/old-guild-root/repositories/app \
  --legacy-root /absolute/path/to/old-guild-root \
  --major-upgrade \
  --config-mode managed \
  --dry-run
~~~

確認後、同じ引数から`--dry-run`だけを外します。

`--legacy-root`は非Git directory、`--target`はその配下にあるcanonical Git rootでなければなりません。JSON出力の`legacy_root`、`archive_paths`、`legacy_actions`、target側の`actions`を確認します。major migrationのdry-runはどちらのrootも変更しません。

installerは旧installerが管理していたことをmarkerやruntime pathから確認し、旧rootの既知legacy pathを旧root内の`.agent-guild-orchestra-archives/v2-to-v3-...`へcold copyしてからactive treeから除去し、v3をtargetへ導入します。旧SQLite queue、Ledger、dashboard、role file、hook、旧Skillはarchive内にのみ残り、3.0から読み込みません。旧`AGENTS.md`はmanaged blockだけを除き、block外の利用者規則を保持します。`repositories/`全体、兄弟repository、未知の`.codex` sibling、third-party Skill、通常のsource codeには触れません。

旧版が例外的に実Git rootへ直接導入されていた場合だけ、`--legacy-root`を省略してそのrootを`--target`へ指定します。この一root移行ではcold archiveを`.agent-guild-orchestra-archives/`に置き、`/.agent-guild-orchestra-archives/`だけを対象にするnarrow local excludeを管理します。旧broad excludeを除きながら利用者patternを保持します。

旧版には導入時のper-file hashがない場合があります。その場合、現在の3.0 template hashを過去のbaselineとして推定しません。旧managed surfaceは内容をcold archiveしてから置換します。既知のlegacy evidenceがない既存fileと新しい配布先が衝突した場合は、unmanaged collisionとして停止します。

## 復元

transaction中の失敗はinstallerが旧rootとtargetの両方を自動で元へ戻し、途中のarchiveを除きます。完了後に旧版へ戻す場合はCodexを止め、新しいmanaged fileを別に保全してから、出力されたarchive directoryの内容をJSON出力の`legacy_root`へ戻します。archive.jsonが退避pathを記録します。

archive directoryをtargetとして再度installerを実行しないでください。通常の`--target`はcanonical Git rootそのものを要求し、`--legacy-root`はmajor migration専用の明示的な非Git rootです。

## 3.0以降の更新

初回3.0導入後はinstall-manifest.jsonに、その導入先へ実際に書いたhashが残ります。配布元だけが変わったfileは更新し、導入先だけが変わったfileは保持します。両方で変わったfileは衝突として停止するため、差分をreviewして手動で統合してください。

AGENTS.mdでは管理blockだけをhash比較し、block外の利用者規則は更新対象に含めません。
