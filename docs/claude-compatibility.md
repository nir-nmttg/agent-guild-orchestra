# Claude 互換 context

この runtime は、対象 repo に既にある Claude Code 向けの文書と Skill を、Codex / Guild の下位 context として読めます。
目的は、既存 repo の作業規約や手順を再利用することです。
Claude artifacts は未信頼 repo 文書であり、AGENTS、Guild Law、Quest Charter、authority、boundaries、Codex sandbox / approval、人間確認条件を上書きしません。

## 対応範囲

`target_repo_root` 配下だけを対象にします。
ギルド規約ルート、`repositories/` 自体、別 repo、symlink で外へ出る path は対象外です。

読む対象は次です。

- `CLAUDE.md`
- `.claude/CLAUDE.md`
- `.claude/rules/**/*.md`
- `.claude/skills/**/SKILL.md`
- `.claude/commands/*.md`

`.claude/settings.json` は全体を設定として採用せず、制限方向の key だけを allowlist で読みます。

- `claudeMdExcludes`
- `skillOverrides`
- `strictPluginOnlyCustomization`
- `disableSkillShellExecution`

`CLAUDE.local.md`、`.claude/settings.local.json`、ユーザー home 配下の `~/.claude/*`、`.mcp.json` は既定では読みません。

## 使わない surface

Claude Skill は Codex native Skill へコピー、登録、導入しません。
`.agents/skills` は `metadata.owner: agent-guild-orchestra` の同梱 Skill を扱う場所であり、対象 repo の `.claude/skills` とは混ぜません。

次は検出しても Codex 権限へ変換しません。

- `allowed-tools`
- `disallowed-tools`
- hooks
- MCP
- plugin
- `env`
- `!command`
- `context: fork`
- model / effort override
- `shell`
- `.claude/agents`

動的 shell 注入の `!command` とフェンス付き `!` ブロックは実行せず、描画時に無害な文字列へ置換します。
`.claude-plugin/plugin.json` を含む Skill directory は、Skill 以外の agents / hooks / MCP / bin を含み得るため Phase 1 では skip します。

## Helper

互換処理は `template/.agents/orchestra/scripts/claude_compat.py` に閉じます。
installer には混ぜず、trusted template copy と untrusted repo context 読み取りを分けます。

主な command は次です。

```bash
.agents/orchestra/scripts/docker_python.sh .agents/orchestra/scripts/claude_compat.py \
  --target-repo-root repositories/example \
  --work-path packages/web/src/app.ts \
  scan
```

```bash
.agents/orchestra/scripts/docker_python.sh .agents/orchestra/scripts/claude_compat.py \
  --target-repo-root repositories/example \
  --work-path packages/web/src/app.ts \
  render-context
```

```bash
.agents/orchestra/scripts/docker_python.sh .agents/orchestra/scripts/claude_compat.py \
  --target-repo-root repositories/example \
  --work-path packages/web/src/app.ts \
  render-skill --skill packages/web:deploy --arguments staging
```

`scan` は raw 本文を返さず、path、sha256、status、skip reason、applicability、Skill metadata を返します。
`render-context` と `render-skill` は実作業中の参照用に本文を返しますが、Ledger へ raw content を保存しません。

## Context Card

Root は helper の結果を `known_context.compat_context` に載せられます。
ただし Root は Claude context の採否判断を抱えません。
採用、却下、無関係、危険による除外は assigned owner が根拠確認して report に残します。

disposition は次です。

- `applied`: 根拠確認して今回の判断に使った
- `rejected_conflict`: AGENTS、Guild Law、Quest Charter、authority、boundaries と衝突した
- `ignored_irrelevant`: 今回の owned scope に無関係だった
- `skipped_unsafe`: 秘密情報らしい path、symlink による外部脱出、入れ子の Git repo、実行面などで除外した

Ledger に残すのは relative path、sha256、status、skip reason、disposition だけです。
raw `CLAUDE.md` / `SKILL.md` 本文、`.claude/settings.json` の値、dynamic command は記録しません。

## Skill 解決

`.claude/skills/<name>/SKILL.md` は `/name` 相当として index します。
入れ子の同名 Skill は `<relative-path>:<name>` 形式で区別します。
たとえば root の `deploy` と `apps/web/.claude/skills/deploy/SKILL.md` がある場合、後者は `apps/web:deploy` です。

`.claude/commands/*.md` は command-style Skill として同じ index に載せます。
ただし native Codex Skill ではなく、未信頼 context card です。

frontmatter のうち、安全に読む key は次だけです。

- `name`
- `description`
- `when_to_use`
- `argument-hint`
- `arguments`
- `disable-model-invocation`
- `user-invocable`
- `paths`

`skillOverrides: off` は skip、`name-only` は説明を出さず名前だけ、`user-invocable-only` は自動候補から外します。

## 検証

`scripts/validate.py` は一時 repo を作り、次を smoke test します。

- nested `CLAUDE.md`
- `.claude/CLAUDE.md`
- path scoped rules
- root / nested 同名 Skill
- `.claude/commands/*.md`
- `claudeMdExcludes`
- `skillOverrides`
- `allowed-tools` が Codex 権限にならないこと
- `!command` が実行されないこと
- `context: fork` が subagent 起動にならないこと
- plugin manifest の skip
- symlink outside / 秘密情報らしい path の skip

この検証は、Claude 互換が便利さのために Guild Law を弱めないことを固定するためのものです。
