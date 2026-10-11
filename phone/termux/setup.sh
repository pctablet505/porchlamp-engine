#!/usr/bin/env bash
# PHONE-1 Termux setup. Installs packages and creates the layout, then PRINTS the manual steps (it does not run them).
set -euo pipefail
PHONE_HOME="${PORCHLAMP_PHONE_HOME:-$HOME/porchlamp}"
ENGINE="${PORCHLAMP_ENGINE:-$HOME/porchlamp-engine}"

pkg update -y && pkg install -y python git gh tmux termux-api

mkdir -p "$PHONE_HOME/raw" "$PHONE_HOME/state/refresh" "$PHONE_HOME/config" "$PHONE_HOME/archives" "$PHONE_HOME/logs"
chmod 700 "$PHONE_HOME/config"
echo "layout ready: $PHONE_HOME"
echo
echo "Manual steps (not run by this script):"
echo "  Both tokens are placed on the phone only: type or paste them here, never put them in a chat, a commit or a"
echo "  message, and never reuse the token the workstation uses."
echo "  1. Put the LAN token (24+ characters, one line) in: $PHONE_HOME/config/lan_token"
echo "  2. Put the GitHub token (one line) in: $PHONE_HOME/config/github_token (refresh_phone.sh exports it as GH_TOKEN)"
echo "     It must be a classic token with NO scopes (it can then see public data only). gh auth login is NOT used."
echo "  3. termux-wake-lock"
echo "  4. Android: turn battery optimisation OFF for Termux and Termux:Boot."
echo "  5. Engine checkout at $ENGINE (git clone of the porchlamp engine)."
echo "  6. Start now, or let Termux:Boot start it: bash $ENGINE/phone/termux/boot.sh"
