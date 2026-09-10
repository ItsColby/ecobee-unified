#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
backend="${2:-container}"
source_git_dir="${3:-}"
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$source_root"
if [[ "$backend" == container ]]; then
  temporary_root="$(mktemp -d)"
  trap 'rm -rf "$temporary_root"' EXIT
  repo_root="$temporary_root/payload"
  history_root="$temporary_root/history.git"
  mkdir "$repo_root"
  source_git=(git -C "$source_root")
  if [[ -n "$source_git_dir" ]]; then
    source_git=(git --git-dir="$source_git_dir" --work-tree="$source_root")
  fi
  if [[ "$("${source_git[@]}" rev-parse --is-shallow-repository)" != false ]]; then
    echo "Complete original Git history is required; source is unavailable or shallow." >&2
    exit 1
  fi
  # Preserve every available original ref and detached candidate HEAD separately
  # from the exact working-tree payload and its synthetic archive index.
  source_head="$("${source_git[@]}" rev-parse --verify HEAD)"
  "${source_git[@]}" bundle create "$temporary_root/history.bundle" --all HEAD
  git clone --quiet --mirror "$temporary_root/history.bundle" "$history_root"
  git -C "$history_root" update-ref --no-deref HEAD "$source_head"
  "${source_git[@]}" ls-files --cached --others --exclude-standard -z |
    while IFS= read -r -d '' path; do
      if [[ -e "$source_root/$path" || -L "$source_root/$path" ]]; then
        printf '%s\0' "$path"
      fi
    done |
    tar -C "$source_root" --null --files-from=- --create --file=- |
    tar -C "$repo_root" --extract --file=-
  # The pinned Actionlint image runs as an unprivileged user.
  chmod a+rx "$repo_root"
  # DrvFS exposes regular files as executable unless metadata is enabled.
  find "$repo_root" -type f -exec chmod a-x {} +
  git -C "$repo_root" init -q
  git -C "$repo_root" config user.name local-validation
  git -C "$repo_root" config user.email local-validation@invalid
  # Discovery already excluded ignored untracked files; retain tracked files
  # even when the candidate adds an ignore rule that now matches them.
  git -C "$repo_root" add --force -A
  git -C "$repo_root" commit -qm snapshot
elif [[ "$backend" != native ]]; then
  echo "Unknown backend: $backend" >&2
  exit 2
fi
python_image="docker.io/library/python@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc"
actionlint_image="docker.io/rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
hassfest_image="ghcr.io/home-assistant/hassfest@sha256:8cd7bdb8f82430c2c13703290b1fc38dcc99957dd76ad3f230035ecee70b672d"

run_python() {
  if [[ "$backend" == native ]]; then
    (cd "$repo_root" && PUBLIC_SAFETY_HISTORY_REPOSITORY="$repo_root" bash -lc "$1")
  else
    podman run --rm \
      -e HOME=/tmp/home -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
      -e PIP_ROOT_USER_ACTION=ignore -e DEBIAN_FRONTEND=noninteractive \
      -e PIP_COMPILE=0 -e PIP_CACHE_DIR=/pip-cache \
      -e PYTHONPYCACHEPREFIX=/tmp/pycache -e XDG_CACHE_HOME=/tmp/cache \
      -e RUFF_CACHE_DIR=/tmp/ruff-cache -e MYPY_CACHE_DIR=/dev/null \
      -e 'PYTEST_ADDOPTS=-p no:cacheprovider' \
      -e PUBLIC_SAFETY_HISTORY_REPOSITORY=/source-history \
      -v "$history_root:/source-history:ro" \
      -v "$repo_root:/workspace:ro" -w /workspace \
      --mount type=volume,source=ecobee-unified-validation-pip,target=/pip-cache \
      "$python_image" bash -lc \
      'apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null && eval "$1"' \
      local-validation "$1"
  fi
}

run_actionlint() {
  if [[ "$backend" == native ]]; then
    local bin
    bin="$(mktemp -d)"
    GOBIN="$bin" go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
    "$bin/actionlint"
    rm -rf "$bin"
  else
    podman run --rm -v "$repo_root:/repo:ro" -w /repo "$actionlint_image"
  fi
}

run_unit() {
  run_actionlint
  # The validation shell expands its history path after entering the container.
  # shellcheck disable=SC2016
  run_python '
    python -m pip install "ruff==0.16.1" "shellcheck-py==0.11.0.1" "zizmor==1.29.0" &&
    zizmor --strict-collection --persona auditor . &&
    shellcheck scripts/verify-release-local.sh &&
    python -m ruff format --check custom_components tests scripts &&
    python -m ruff check custom_components tests scripts &&
    python -m unittest tests.test_public_safety tests.test_parallel_validation &&
    python -m compileall -q custom_components/ecobee_unified tests scripts &&
    python scripts/check_public_safety.py --history-repository "$PUBLIC_SAFETY_HISTORY_REPOSITORY"
  '
}

run_minimum() {
  run_python '
    python -m pip install "pytest-homeassistant-custom-component==0.13.354" &&
    python -m pip install --upgrade -r requirements-ha-test.txt &&
    python -m pip install "mypy==2.3.0" &&
    python -m pip check &&
    python -m mypy --strict custom_components/ecobee_unified &&
    pytest tests -q
  '
}

run_current() {
  run_python '
    python -m pip install "pytest-homeassistant-custom-component==0.13.364" &&
    python -m pip install --upgrade -r requirements-ha-current.txt &&
    python -m pip install "mypy==2.3.0" &&
    python -m pip check &&
    python -m mypy --strict custom_components/ecobee_unified &&
    pytest tests -q
  '
}

run_ha_matrix() {
  if [[ "$backend" == native ]]; then
    run_minimum
    run_current
  else
    # Containers read one immutable payload; installed environments stay separate.
    local minimum_pid current_pid minimum_status=0 current_status=0
    run_minimum & minimum_pid=$!
    run_current & current_pid=$!
    # Always reap both lanes before the parent can remove the payload/history.
    wait "$minimum_pid" || minimum_status=$?
    wait "$current_pid" || current_status=$?
    printf 'Home Assistant lanes: minimum=%s current=%s\n' "$minimum_status" "$current_status"
    (( minimum_status == 0 && current_status == 0 ))
  fi
}

run_release() {
  if [[ "$backend" == native ]]; then
    docker run --rm -v "$repo_root:/github/workspace:ro" "$hassfest_image"
  else
    podman run --rm -v "$repo_root:/github/workspace:ro" "$hassfest_image"
  fi
}

case "$mode" in
  all) run_unit; run_ha_matrix; run_release ;;
  unit) run_unit ;;
  minimum) run_minimum ;;
  current) run_current ;;
  release) run_release ;;
  *) echo "Unknown mode: $mode" >&2; exit 2 ;;
esac
