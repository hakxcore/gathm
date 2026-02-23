#!/bin/bash
## Gathm macOS Installer

set -e

currentVersion="3.0.0"
INSTALL_DIR="/usr/local/share/gathm"
BIN_DIR="/usr/local/bin"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "🚀 Installing Gathm v$currentVersion for macOS..."

# Check for Homebrew
if ! command -v brew &>/dev/null; then
    echo "❌ Homebrew not found. Please install Homebrew first:"
    echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
brew install coreutils jq pv curl wget python3 jp2a dialog 2>/dev/null || true

# Create directories
echo "📁 Creating directories..."
sudo mkdir -p "$INSTALL_DIR"
sudo mkdir -p "$BIN_DIR"

# Copy files
echo "📋 Copying files..."
sudo cp -r "$SCRIPT_DIR/"* "$INSTALL_DIR/"

# Create symlink
echo "🔗 Creating symlink..."
sudo ln -sf "$INSTALL_DIR/gathm" "$BIN_DIR/gathm"
sudo chmod +x "$BIN_DIR/gathm"

# Success message
echo ""
echo "✅ Gathm v$currentVersion installed successfully!"
echo ""
echo "Run 'gathm' to start using it."
echo ""
