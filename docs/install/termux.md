# Termux Installation

This is the clean, supported installation path for Android Termux.

## Prerequisites

- Install **Termux** (recommended from F-Droid/GitHub releases).
- Open Termux and ensure network access is available.

## 1) Clone the repository

```bash
pkg update -y
pkg install -y git
git clone https://github.com/hakxcore/gathm.git
cd gathm
```

## 2) Run installer

```bash
bash install.sh
```

## 3) Reload shell

```bash
source ~/.bashrc
```

If using zsh:

```bash
source ~/.zshrc
```

## 4) Verify

```bash
gathm-agent status
gathm-agent list
gathm-agent health all
```

## 5) Run

```bash
gathm                      # menu launcher
gathm-agent ask "weather in Mumbai"
gathm-api --port 8080
```

## Troubleshooting

### Storage permission not configured

Run manually:

```bash
termux-setup-storage
```

Then rerun setup:

```bash
bash install.sh
```

### Command not found after install

Ensure `~/.local/bin` is in `PATH`:

```bash
echo "$PATH"
grep -n ".local/bin" ~/.bashrc ~/.zshrc 2>/dev/null
```

If missing:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Check environment only

```bash
bash install.sh --check
```
