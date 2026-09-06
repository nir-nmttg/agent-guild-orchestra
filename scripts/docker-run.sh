#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

fail() { printf 'orchestra: %s\n' "$*" >&2; exit 1; }

directory() {
  local path="$1"
  case "$path" in
    '~') path="$HOME" ;;
    '~/'*) path="$HOME/${path#\~/}" ;;
  esac
  while [[ "$path" != / ]]; do
    case "$path" in
      */.) path="${path%/.}" ;;
      */) path="${path%/}" ;;
      *) break ;;
    esac
    [[ -n "$path" ]] || path=/
  done
  [[ ! -L "$path" ]] || fail "directory itself may not be a symlink: $path"
  [[ -d "$path" ]] || fail "directory does not exist: $path"
  (cd "$path" && pwd -P)
}

git_at() (
  # Discard inherited repository selectors/config injection for boundary checks.
  for variable in $(compgen -e); do
    case "$variable" in GIT_*) unset "$variable" ;; esac
  done
  git -C "$1" "${@:2}"
)

mount_paths=()
mount_modes=()
add_mount() {
  local path="$1" mode="$2" i
  for ((i = 0; i < ${#mount_paths[@]}; i++)); do
    if [[ "${mount_paths[$i]}" == "$path" ]]; then
      [[ "$mode" != rw ]] || mount_modes[$i]=rw
      return
    fi
  done
  mount_paths+=("$path")
  mount_modes+=("$mode")
}

mode="${1:-}"
[[ $# -gt 0 ]] && shift
args=()
target=""
child=""
source_path=""
dry_run=false
case "$mode" in
  install|cleanup)
    while [[ $# -gt 0 ]]; do
      option="$1"
      case "$option" in
        --target|--child|--source)
          [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$option requires a directory"
          value="$2"
          shift 2
          ;;
        --target=*|--child=*|--source=*)
          value="${option#*=}"
          option="${option%%=*}"
          shift
          ;;
        *)
          [[ "$option" != --dry-run ]] || dry_run=true
          args+=("$option")
          shift
          continue
          ;;
      esac
      value="$(directory "$value")"
      case "$option" in
        --target) target="$value" ;;
        --child) child="$value" ;;
        --source) source_path="$value" ;;
      esac
    done
    write_mode=rw
    [[ "$dry_run" != true ]] || write_mode=ro
    if [[ -n "$target" ]]; then
      command -v git >/dev/null 2>&1 || fail "Git is required on the host"
      ancestor="$target"
      while :; do
        [[ ! -e "$ancestor/.git" && ! -L "$ancestor/.git" ]] || fail "--target must be a non-Git parent outside all Git working trees"
        [[ "$ancestor" != / ]] || break
        ancestor="$(dirname "$ancestor")"
      done
      if git_at "$target" rev-parse --git-dir >/dev/null 2>&1; then
        fail "--target must be a non-Git parent outside all Git working trees"
      fi
      add_mount "$target" "$write_mode"
      # Ordinary install/update cannot write through to any child repository.
      [[ ! -L "$target/repositories" ]] || fail "repositories/ may not be a symlink"
      [[ ! -d "$target/repositories" ]] || add_mount "$target/repositories" ro
      args+=(--target "$target")
    fi
    if [[ -n "$child" ]]; then
      [[ "$mode" == cleanup && -n "$target" && "$child" == "$target/repositories/"* ]] || fail "--child is only for cleanup beneath the explicit parent/repositories/"
      git_root="$(git_at "$child" rev-parse --show-toplevel)"
      [[ "$(directory "$git_root")" == "$child" ]] || fail "--child must be the Git root"
      add_mount "$child" "$write_mode"
      # Even explicit cleanup may never change the index or Git configuration.
      add_mount "$child/.git" ro
      for flag in --absolute-git-dir --git-common-dir; do
        git_dir="$(git_at "$child" rev-parse "$flag")"
        [[ "$git_dir" == /* ]] || git_dir="$child/$git_dir"
        add_mount "$(directory "$git_dir")" ro
      done
      args+=(--child "$child")
    fi
    if [[ -n "$source_path" ]]; then
      add_mount "$source_path" ro
      for category in maintainer-skills optional-skills; do
        package_dir="$(dirname "$source_path")/$category"
        [[ ! -d "$package_dir" ]] || add_mount "$(directory "$package_dir")" ro
      done
      args+=(--source "$source_path")
    fi
    program="$SCRIPT_DIR/install.py"
    [[ "$mode" != cleanup ]] || program="$SCRIPT_DIR/cleanup_child.py"
    ;;
  validate)
    [[ $# == 0 ]] || fail "validate does not accept arguments"
    program="$SCRIPT_DIR/validate.py"
    ;;
  *) fail "expected install, cleanup or validate" ;;
esac

add_mount "$ROOT" ro
command -v docker >/dev/null 2>&1 || fail "Docker is required; install and start Docker Desktop or Docker Engine"
docker info --format '{{.ServerVersion}}' >/dev/null || fail "Docker Engine is unavailable; start it and retry"

# The build context contains only the Dockerfile, never repository/user content.
image_id="$(docker build --quiet "$ROOT/docker")"
# Recovery backups live in the mounted parent and survive container removal.
run_args=(run --rm --network none --user "$(id -u):$(id -g)")
for ((i = 0; i < ${#mount_paths[@]}; i++)); do
  # Quote CSV fields for --mount so spaces, commas and quotes remain literal.
  escaped="${mount_paths[$i]//\"/\"\"}"
  mount="type=bind,\"source=$escaped\",\"target=$escaped\""
  [[ "${mount_modes[$i]}" != ro ]] || mount+=",readonly"
  run_args+=(--mount "$mount")
done
run_args+=(--workdir "$ROOT" "$image_id" python3 "$program")
# macOS ships Bash 3.2, where expanding an empty array with nounset fails.
if [[ ${#args[@]} -gt 0 ]]; then
  run_args+=("${args[@]}")
fi
exec docker "${run_args[@]}"
