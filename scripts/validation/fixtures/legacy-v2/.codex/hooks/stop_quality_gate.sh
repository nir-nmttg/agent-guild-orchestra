#!/usr/bin/env bash
# Stop hook の Python 実行を Docker runner に限定する薄い wrapper。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUILD_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNNER="$GUILD_ROOT/.agents/orchestra/scripts/docker_python.sh"
HOOK="$SCRIPT_DIR/stop_quality_gate.py"
IMAGE="${AGENT_GUILD_ORCHESTRA_DOCKER_IMAGE:-agent-guild-orchestra-runtime:local}"

emit_skip() {
  if [ "${AGENT_GUILD_ORCHESTRA_STOP_QUALITY_STRICT:-0}" = "1" ]; then
    printf '%s\n' '{"continue":false,"decision":"block","reason":"agent-guild-orchestra: Docker runner で Stop hook を実行できません。Docker を起動してください。"}'
  else
    printf '%s\n' '{"continue":true,"systemMessage":"agent-guild-orchestra: Docker runner で Stop hook を実行できないためスキップしました。Docker を起動してください。"}'
  fi
}

if [ ! -x "$RUNNER" ] || [ ! -f "$HOOK" ]; then
  emit_skip
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  emit_skip
  exit 0
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  emit_skip
  exit 0
fi

set +e
OUTPUT="$(AGENT_GUILD_ORCHESTRA_DOCKER_SKIP_BUILD=1 "$RUNNER" "$HOOK" 2>/dev/null)"
STATUS=$?
set -e

if [ "$STATUS" -eq 0 ]; then
  if [ -n "$OUTPUT" ]; then
    printf '%s\n' "$OUTPUT"
  fi
  exit 0
fi

emit_skip
