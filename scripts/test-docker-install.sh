#!/usr/bin/env bash
# Real Docker smoke tests on disposable parents; no host Python is invoked.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/orchestra-docker-smoke.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
fixture="$(cd "$fixture" && pwd -P)"
parent="$fixture/asked root"
child="$parent/repositories/app with spaces, and comma"
mkdir -p "$child"
git -C "$child" init -q
printf 'base\n' > "$child/source.txt"
git -C "$child" add source.txt
git -C "$child" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm fixture
printf 'staged\n' > "$child/source.txt"
git -C "$child" add source.txt
printf 'unstaged\n' > "$child/source.txt"
printf 'untracked\n' > "$child/local.txt"
linked="$parent/repositories/linked worktree"
git -C "$child" worktree add --detach -q "$linked" HEAD
cp -R "$parent/repositories" "$fixture/before"

# Populate recognized v2 files without relying on host Python or Git history.
cp -R "$SCRIPT_DIR/validation/fixtures/legacy-v2/.codex" "$parent/.codex"
cp -R "$SCRIPT_DIR/validation/fixtures/legacy-v2/.agents" "$parent/.agents"
chmod 755 "$parent/.codex/hooks/stop_quality_gate.sh"
bash "$SCRIPT_DIR/install.sh" --target "$parent" --dry-run > "$fixture/dry.json"
[[ ! -e "$parent/AGENTS.md" ]]
[[ -f "$parent/.codex/agents/captain.toml" ]]
bash "$SCRIPT_DIR/install.sh" --target "$parent" > "$fixture/install.json"
[[ -f "$parent/.agents/orchestra/install-manifest.json" ]]
[[ -O "$parent/.agents/orchestra/install-manifest.json" ]]
[[ ! -e "$parent/.codex/agents/captain.toml" ]]
[[ -d "$parent/.agent-guild-orchestra-archives" ]]
bash "$SCRIPT_DIR/sync.sh" --target "$parent" > "$fixture/sync.json"
diff -r "$fixture/before" "$parent/repositories"
[[ ! -e "$child/.codex" && ! -e "$linked/AGENTS.md" ]]

# Reconstruct a prior child v3 manifest only inside this disposable fixture.
cp "$parent/AGENTS.md" "$linked/AGENTS.md"
cp -R "$parent/.codex" "$linked/.codex"
cp -R "$parent/.agents" "$linked/.agents"
image_id="$(docker build --quiet "$SCRIPT_DIR/../docker")"
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$fixture,target=/fixture" "$image_id" \
  python3 -c 'import json; from pathlib import Path; p=Path("/fixture/asked root/repositories/linked worktree/.agents/orchestra/install-manifest.json"); value=json.loads(p.read_text()); value["schema"]=1; value.pop("layout"); p.write_text(json.dumps(value))'
cp -R "$parent/repositories" "$fixture/with-child-v3"
bash "$SCRIPT_DIR/sync.sh" --target "$parent" > "$fixture/sync-with-child.json"
bash "$SCRIPT_DIR/cleanup-child.sh" --target "$parent" --child "$linked" --dry-run > "$fixture/cleanup-dry.json"
diff -r "$fixture/with-child-v3" "$parent/repositories"
bash "$SCRIPT_DIR/cleanup-child.sh" --target "$parent" --child "$linked" > "$fixture/cleanup.json"
[[ ! -e "$linked/AGENTS.md" && ! -e "$linked/.agents/orchestra/install-manifest.json" ]]
diff -r "$fixture/before" "$parent/repositories"

# Exercise real SIGINT delivery inside the container and a second storage error
# during restore. The latter's recovery copy must remain after Docker --rm.
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$SCRIPT_DIR/..,target=/distribution,readonly" \
  --mount "type=bind,source=$fixture,target=/fixture" "$image_id" \
  python3 /distribution/tests/docker_recovery_probe.py /fixture
[[ -f "$fixture/interrupted/AGENTS.md" ]]
[[ ! -e "$fixture/interrupted/.agent-guild-orchestra-recovery" ]]
recovery=("$fixture/restore-failed/.agent-guild-orchestra-recovery/"transaction-*)
[[ ${#recovery[@]} == 1 && -f "${recovery[0]}/AGENTS.md" && -f "${recovery[0]}/recovery.json" ]]
printf 'docker parent install, sync, migration and explicit child cleanup: ok\n'
