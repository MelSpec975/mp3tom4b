#!/usr/bin/env bash
# Install or remove mp3tom4b (command line + desktop app).
#   sudo ./install.sh              install to /usr/local
#   sudo ./install.sh --uninstall  remove it again
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
LIB="$PREFIX/lib/mp3tom4b"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
HERE="$(cd "$(dirname "$0")" && pwd)"

refresh() {
  command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$LIB"
  rm -f "$BIN/mp3tom4b" "$BIN/mp3tom4b-gui" "$APPS/mp3tom4b.desktop" "$ICONS/mp3tom4b.svg"
  refresh
  echo "mp3tom4b removed."
  exit 0
fi

if [[ $EUID -ne 0 && "$PREFIX" == /usr* ]]; then
  echo "Run with sudo: sudo ./install.sh" >&2
  exit 1
fi

install -d "$LIB" "$BIN" "$APPS" "$ICONS"
install -m 755 "$HERE/mp3tom4b.py" "$HERE/mp3tom4b_gui.py" "$LIB/"
ln -sf "$LIB/mp3tom4b.py" "$BIN/mp3tom4b"
ln -sf "$LIB/mp3tom4b_gui.py" "$BIN/mp3tom4b-gui"
install -m 644 "$HERE/data/mp3tom4b.desktop" "$APPS/"
install -m 644 "$HERE/data/mp3tom4b.svg" "$ICONS/"
refresh

echo "Installed. Run 'mp3tom4b' in a terminal, or open 'MP3 to M4B' from your app menu."

missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
python3 -c "import PySide6" 2>/dev/null || missing+=(pyside6)
if (( ${#missing[@]} )); then
  echo
  echo "Still needed: ${missing[*]}"
  echo "  Arch:           sudo pacman -S ${missing[*]}"
  echo "  Debian/Ubuntu:  sudo apt install ffmpeg python3-pyside6.qtwidgets"
  echo "  Fedora:         sudo dnf install ffmpeg python3-pyside6"
fi
