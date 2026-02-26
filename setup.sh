#!/usr/bin/env bash
# Backward-compatible wrapper.
# Canonical installer: install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
echo "Notice: 'setup.sh' is deprecated. Use 'install.sh'."
exec bash "$SCRIPT_DIR/install.sh" "$@"
