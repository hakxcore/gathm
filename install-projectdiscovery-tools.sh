#!/usr/bin/env bash
# Install ProjectDiscovery binaries used by gathm wrappers.

set -euo pipefail

BIN_DIR="${BIN_DIR:-${HOME}/go/bin}"
GOPATH_DIR="${GOPATH:-${HOME}/go}"

if ! command -v go >/dev/null 2>&1; then
  echo "Error: Go is not installed. Install Go first: https://go.dev/doc/install" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
mkdir -p "$GOPATH_DIR"

export GOPATH="$GOPATH_DIR"
export GOBIN="$BIN_DIR"

echo "Installing ProjectDiscovery tools to: $GOBIN"

go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/projectdiscovery/naabu/v2/cmd/naabu@latest
go install github.com/projectdiscovery/katana/cmd/katana@latest
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install github.com/projectdiscovery/uncover/cmd/uncover@latest

echo

echo "Installed binaries:"
for b in subfinder dnsx httpx naabu katana nuclei uncover; do
  if [[ -x "$GOBIN/$b" ]]; then
    echo "  - $GOBIN/$b"
  fi
done

echo
echo "Add to PATH if needed:"
echo "  export PATH=\"$GOBIN:\$PATH\""

echo
echo "Note: 'strix' is not a ProjectDiscovery binary and must be installed separately."
