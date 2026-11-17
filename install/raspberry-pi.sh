#!/bin/sh
# Install somame-local's web office as a service on a Raspberry Pi
# (Raspberry Pi OS, 64-bit, Pi 4 with 4 GB or more) or any Debian or Ubuntu
# machine: a laptop, a mini PC, a home server.
#
#   sh install/raspberry-pi.sh ~/office "bookkeeper"
#
# It installs Python's venv, puts somame-core in ~/.somame/venv, writes
# ~/.somame/office.env for your model settings (readable only by you),
# and starts a user service that runs the office in the folder you name.
# Nothing here runs as root except apt-get. Re-running it upgrades.
set -eu

FOLDER="${1:-$HOME/somame-office}"
ROLE="${2:-general assistant}"
PORT="${SOMAME_PORT:-8765}"
VERSION="${SOMAME_VERSION:-0.2.0}"
HOME_DIR="$HOME/.somame"
VENV="$HOME_DIR/venv"
ENV_FILE="$HOME_DIR/office.env"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/somame-office.service"

say() { printf '%s\n' "$*"; }

if ! command -v systemctl >/dev/null 2>&1; then
    say "This installer needs systemd (Raspberry Pi OS, Debian, Ubuntu)."
    exit 1
fi

say "Installing Python's venv (asks for your password once)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv

say "Installing somame-core $VERSION into $VENV..."
mkdir -p "$HOME_DIR" "$FOLDER" "$UNIT_DIR"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade "somame-core==$VERSION"

if [ ! -f "$ENV_FILE" ]; then
    umask 077
    cat > "$ENV_FILE" <<'EOF'
# The model the office thinks with. Uncomment one.
#
# Your own Anthropic key:
# ANTHROPIC_API_KEY=sk-ant-...
#
# Or a model you run yourself (Ollama on this machine), with no key:
# SOMAME_BASE_URL=http://localhost:11434/v1
# SOMAME_MODEL=llama3.1
#
# Optional oversight from a Somame Cloud workspace:
# SOMAME_CLOUD_URL=https://app.somame.example
# SOMAME_CLOUD_KEY=somame_local_...
EOF
    umask 022
    say "Wrote $ENV_FILE: put your model settings in it."
fi

cat > "$UNIT" <<EOF
[Unit]
Description=Somame local office ($ROLE)
After=network-online.target

[Service]
EnvironmentFile=$ENV_FILE
WorkingDirectory=$FOLDER
ExecStart=$VENV/bin/somame-local --folder "$FOLDER" --role "$ROLE" --serve --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now somame-office.service
# Keep it running when you log out.
loginctl enable-linger "$USER" 2>/dev/null || true

say ""
say "The office is starting. Its link, with the token, is in:"
say "  journalctl --user -u somame-office -n 20"
say "or open http://$(hostname -I 2>/dev/null | cut -d' ' -f1):$PORT/?token=\$(cat $FOLDER/.somame/office.token)"
say ""
say "To use a model on this machine, install Ollama from https://ollama.com, run"
say "'ollama pull llama3.1', and set SOMAME_BASE_URL in $ENV_FILE. Then:"
say "  systemctl --user restart somame-office"
