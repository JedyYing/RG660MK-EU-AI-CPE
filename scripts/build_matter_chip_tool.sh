#!/usr/bin/env bash
# Reproducible aarch64 static chip-tool build for the RG660MK-EU.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
SDK_DIR=${SDK_DIR:-"$PROJECT_DIR/.build/connectedhomeip"}
OUT_DIR=${OUT_DIR:-"$SDK_DIR/out/rg660-chip-tool"}
ARTIFACT_DIR=${ARTIFACT_DIR:-"$PROJECT_DIR/artifacts/matter-v1.6.0.0-rg660"}
SDK_TAG=v1.6.0.0
SDK_COMMIT=250a9e6c50ee2068107f3c4808b680f5f2925415
BUILD_IMAGE=${BUILD_IMAGE:-"ubuntu@sha256:786a8b558f7be160c6c8c4a54f9a57274f3b4fb1491cf65146521ae77ff1dc54"}
TOOLCHAIN_IMAGE=${TOOLCHAIN_IMAGE:-"rg660-matter-build:ubuntu24.04-v1"}

if ! command -v git >/dev/null 2>&1 || ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: git and Docker are required on the Ubuntu build host" >&2
    exit 2
fi
if [ ! -d "$SDK_DIR/.git" ]; then
    mkdir -p "$(dirname "$SDK_DIR")"
    git clone --depth 1 --branch "$SDK_TAG" https://github.com/project-chip/connectedhomeip.git "$SDK_DIR"
fi
ACTUAL_COMMIT=$(git -C "$SDK_DIR" rev-parse HEAD)
if [ "$ACTUAL_COMMIT" != "$SDK_COMMIT" ]; then
    echo "ERROR: expected connectedhomeip $SDK_COMMIT, got $ACTUAL_COMMIT" >&2
    exit 2
fi

python3 "$SDK_DIR/scripts/checkout_submodules.py" --shallow --platform linux
mkdir -p "$ARTIFACT_DIR"

if ! docker image inspect "$TOOLCHAIN_IMAGE" >/dev/null 2>&1; then
    docker build --pull=false --build-arg "BASE_IMAGE=$BUILD_IMAGE" -t "$TOOLCHAIN_IMAGE" - <<'DOCKERFILE'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
       ca-certificates git curl unzip xz-utils file pkg-config cmake ninja-build \
       python3 python3-dev python3-pip python3-venv gcc g++ gcc-aarch64-linux-gnu \
       g++-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*
DOCKERFILE
fi

# A host-created Pigweed venv may link to a Python runtime outside SDK_DIR.
# Mount that runtime at the same absolute path so its launchers work in Docker.
PYTHON_MOUNT_ARGS=()
VENV_PYTHON="$SDK_DIR/.environment/pigweed-venv/bin/python3"
if [ -e "$VENV_PYTHON" ]; then
    RESOLVED_VENV_PYTHON=$(readlink -f "$VENV_PYTHON")
    PYTHON_RUNTIME_DIR=$(dirname "$(dirname "$RESOLVED_VENV_PYTHON")")
    PYTHON_MOUNT_ARGS=(-v "$PYTHON_RUNTIME_DIR:$PYTHON_RUNTIME_DIR:ro")
fi

docker run --rm \
    --network host \
    "${PYTHON_MOUNT_ARGS[@]}" \
    -e HOST_UID="$(id -u)" \
    -e HOST_GID="$(id -g)" \
    -e SDK_DIR="$SDK_DIR" \
    -v "$SDK_DIR:$SDK_DIR" \
    -w "$SDK_DIR" \
    "$TOOLCHAIN_IMAGE" \
    bash -lc '
set -eo pipefail
git config --global --add safe.directory "$SDK_DIR"
git config --global --add safe.directory "$SDK_DIR/third_party/pigweed/repo"
source scripts/activate.sh
scripts/examples/gn_build_example.sh examples/chip-tool out/rg660-chip-tool \
    target_cpu=\"arm64\" \
    is_clang=false \
    chip_crypto=\"mbedtls\" \
    chip_config_network_layer_ble=false \
    chip_enable_thread=false \
    chip_enable_wifi=false \
    chip_enable_ethernet=false \
    chip_mdns=\"minimal\" \
    config_use_interactive_mode=false \
    config_enable_yaml_tests=false \
    config_enable_https_requests=false \
    matter_enable_tracing_support=false \
    matter_commandline_enable_perfetto_tracing=false \
    symbol_level=0 \
    strip_symbols=true \
    enable_pie=false \
    target_ldflags='"'"'["-static","-Wl,--no-fatal-warnings"]'"'"'
aarch64-linux-gnu-strip out/rg660-chip-tool/chip-tool
file out/rg660-chip-tool/chip-tool
dpkg-query -W -f='"'"'${Package}=${Version}\n'"'"' | sort > out/rg660-chip-tool/BUILD_PACKAGES.txt
chown -R "$HOST_UID:$HOST_GID" out/rg660-chip-tool
'

install -m 0755 "$OUT_DIR/chip-tool" "$ARTIFACT_DIR/chip-tool"
sha256sum "$ARTIFACT_DIR/chip-tool" > "$ARTIFACT_DIR/SHA256SUMS"
{
    echo "connectedhomeip_tag=$SDK_TAG"
    echo "connectedhomeip_commit=$SDK_COMMIT"
    echo "build_image=$BUILD_IMAGE"
    file "$ARTIFACT_DIR/chip-tool"
    readelf -l "$ARTIFACT_DIR/chip-tool" | grep 'Requesting program interpreter' || echo "program_interpreter=none_static"
} > "$ARTIFACT_DIR/BUILD_INFO.txt"
echo "Built $ARTIFACT_DIR/chip-tool"
