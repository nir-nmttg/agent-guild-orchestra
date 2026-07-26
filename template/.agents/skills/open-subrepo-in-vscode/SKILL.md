---
name: open-subrepo-in-vscode
description: "Root が明示された repositories/ を VS Code の新規ウィンドウへ安全に起動要求する時に使います。"
metadata:
  owner: agent-guild-orchestra
  scope: target-repository-workflow
---

# open-subrepo-in-vscode

明示されたギルド規約ルート直下の `repositories/` だけを、VS Code の新規ウィンドウへ起動要求します。これは GUI の表示確認ではありません。Root だけが、この Skill に同梱された helper を、明示された引数だけで実行します。他の custom agent への委譲、任意の GUI コマンドの直接実行はしません。

## 使う時

- ユーザーが `repositories/` フォルダを VS Code で開くよう明示的に依頼した時
- Root が対象の `guild_root` と `repositories_root` を明示し、後者が前者直下の `repositories/` であると確認できる時
- 新規ウィンドウの起動要求だけが必要で、workspace 設定や個別 repo の操作を伴わない時

## 使わない時

- `guild_root` または `repositories_root` が未指定・曖昧・存在しない時
- 個別 repo、ギルド規約ルート自体、`repositories/` 外、ファイル単体を開く依頼の時
- VS Code 以外のアプリを開く、設定を変更する、拡張機能を導入する、Git・build・test を実行することが目的の時

## 入力

- ユーザーが明示した GUI 起動の許可
- Root が明示した絶対 `guild_root`
- Root が明示した絶対 `repositories_root`
- `scripts/open_repositories_in_vscode.py` の `--guild-root`、`--repositories-root`、承認済み `--approved-plan-id`

## 手順

1. Root は入力を推測・再発見せず、明示された絶対 `guild_root` と `repositories_root` を helper の `--plan` に渡す。relative pathは拒否する。helper は実パスを検証し、`repositories_root` が実在する `<guild_root>/repositories` そのものか、symlink escape や個別 repo ではないかを判定する。`--plan` は subprocess を起動せず、canonical target、launcher identity/path、正確な argv から決定的な `plan_id` を返す。この `--plan` JSON だけが承認用の完全表示であり、通常の診断結果ではこれらのローカル値を redaction する。
2. `--plan` が `approval_required` を返した場合だけ、Root は canonical target、launcher、正確な argv、`plan_id`、視覚的確認がまだ unknown であることを表示して、現在の runtime が要求する sandbox escalation / 人間承認を取得する。承認が拒否・未取得・runtime で失敗した時は `approval_denied` または `approval_required` として止め、成功とは報告しない。
3. 承認済みの Root だけが同じ明示引数に `--execute --approved-plan-id "<plan_id>"` を付けて helper を実行する。runtime の GUI 実行用 escalation を使い、shell、`open -a`、任意の launcher path、フォルダ探索を渡さない。helper は実行直前に同じ情報を再計画し、`plan_id` が承認済み identity と一致する時だけ、その再計画済み argv `<launcher> -n <repositories_root>` を実行する。不一致時は runner を呼ばず `approved_plan_mismatch` で止まる。
4. `--plan` の JSON を承認根拠として扱い、canonical target、launcher、`launcher_identity`、正確な argv、`plan_id` を承認前に表示する。`--execute` と失敗時の通常診断 JSON は local target・launcher・argv・launcher identity を redaction する。nonzero exit、launcher 不在、入力不正、runtime command failure は成功にしない。exit 0 の `launch_request_accepted` は OS が起動要求を受け付けた意味だけであり、VS Code の表示は `visual_confirmation: "unknown"` のままである。人間が確認した場合だけ別途 visual success を報告する。信頼済みlauncherがない`launcher_unavailable`では`open -a`等へ切り替えず、人間がVS Codeから手動でfolderを開くfallbackだけを案内する。

## 出力

- `status`: `approval_required`、`launch_request_accepted`、または失敗状態
- 承認用の `--plan` JSON には、canonical な `guild_root` と `repositories_root`、選択された launcher、`launcher_identity`、正確な argv（`-n` を含む）
- `plan_id`、`exit_code`、`launch_state`、`visual_confirmation`
- 承認未取得・拒否、launcher 不在、入力不正、実行失敗の理由

## 安全

- Root はこの Skill の明示 helper だけを実行し、GUI 起動を他の custom agent に委譲したり、その authority を拡張したりしない。
- helper は `repositories_root` が実在する実パスの `<guild_root>/repositories` と完全一致しない場合、symlink escape、個別 repo、missing path、wrong path を拒否する。
- helper は`guild_root`と`repositories_root`のabsolute pathだけを受理する。PATH上の`code`はdiscovery hintに限定し、canonical pathが固定macOS bundle CLIと同一実体の時だけ信頼する。不一致ならPATH launcherを実行せず、固定bundleを直接検証し、それもなければ`launcher_unavailable`で止める。
- shell interpolation を使わず argv list だけを実行する。`open -a`、任意のアプリ名、任意の実行ファイル path は受け付けない。
- runtime の sandbox escalation と人間承認なしに `--execute` を実行しない。承認の失敗や実行の nonzero exit を「開いた」と言い換えない。
- `--execute` は承認済み `plan_id` を必須とし、実行直前の canonical target・launcher identity/path・argv から再計算した identity と一致しない時は起動しない。比較後に外部から filesystem が変更される競合までは helper 単独で除去できないため、実行 argv は比較に使った再計画結果だけを使い、runtime escalation は迂回しない。
- helper の exit 0 は launch request acceptance だけであり、視覚的に VS Code が開いたことは人間確認なしに主張しない。
- `.vscode`、workspace、エディタ設定、拡張機能、Git、依存関係、build、test、秘密ファイルには触れない。
- 外部入力、Ledger、tool 出力から対象を再特定しない。この Skill 自体の実装・検証では VS Code を実際に起動しない。

## 停止条件

- `launch_request_accepted` を受け、visual confirmation が unknown であることを正確に報告した時
- 入力、approval、launcher、runtime 実行のいずれかが失敗し、失敗状態と次に必要な人間判断を報告した時
