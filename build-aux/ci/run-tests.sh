#!/usr/bin/env bash
# Run the meson suite the way CI does, inside a cached Fedora rawhide image.
#
#   ./build-aux/ci/run-tests.sh              # full suite (-j1)
#   ./build-aux/ci/run-tests.sh --rebuild    # refresh babble-ci:local
#   ./build-aux/ci/run-tests.sh 'compose widgets'   # one test by name
#
# The image keeps installed packages between runs; only the repo is mounted.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${BABBLE_CI_IMAGE:-babble-ci:local}"
DOCKERFILE="$ROOT/build-aux/ci/Dockerfile"

rebuild=false
extra_args=()
for arg in "$@"; do
    case "$arg" in
        --rebuild) rebuild=true ;;
        *) extra_args+=("$arg") ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required" >&2
    exit 1
fi

if $rebuild; then
    echo "Rebuilding $IMAGE..."
    docker build -t "$IMAGE" -f "$DOCKERFILE" "$(dirname "$DOCKERFILE")"
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Building $IMAGE (first run; later runs reuse it -- pass --rebuild to refresh)..."
    docker build -t "$IMAGE" -f "$DOCKERFILE" "$(dirname "$DOCKERFILE")"
fi

# Meson test names may contain spaces; pass them as extra argv to the inner script.
docker run --rm \
    -v "$ROOT:/src:rw" \
    -w /src \
    "$IMAGE" \
    bash /src/build-aux/ci/run-tests-inner.sh "${extra_args[@]}"
