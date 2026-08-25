#!/usr/bin/env bash
# Installs/starts the reviewed mt5-mac-bridge helper at a pinned revision.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This helper is only for macOS."
  exit 1
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
BRIDGE_DIR="${AUTOTRADER_BRIDGE_DIR:-$PROJECT_DIR/.mt5-bridge-runtime}"
BRIDGE_REPOSITORY="https://github.com/theauheral/mt5-mac-bridge.git"
BRIDGE_REVISION="1e8450748d0eaea47a324bbb8d77238061c67bd2"

prepare() {
  if [ ! -d "$BRIDGE_DIR/.git" ]; then
    git clone --filter=blob:none "$BRIDGE_REPOSITORY" "$BRIDGE_DIR"
  fi
  git -C "$BRIDGE_DIR" fetch --depth 1 origin "$BRIDGE_REVISION"
  git -C "$BRIDGE_DIR" checkout --detach "$BRIDGE_REVISION"
}

case "${1:-}" in
  provision)
    prepare
    bash "$BRIDGE_DIR/scripts/mt5_native_bridge.sh" provision
    ;;
  serve)
    prepare
    bash "$BRIDGE_DIR/scripts/mt5_native_bridge.sh" serve
    ;;
  verify)
    prepare
    bash "$BRIDGE_DIR/scripts/mt5_native_bridge.sh" verify
    ;;
  *)
    echo "Usage: $0 {provision|serve|verify}"
    exit 1
    ;;
esac
