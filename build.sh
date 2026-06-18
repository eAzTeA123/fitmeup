#!/usr/bin/env bash
# Render Build Command: bash build.sh
#
# 1) Installiert die Python-Abhaengigkeiten aus requirements.txt
# 2) Holt main.py + das lidl/-Paket (LidlPlus-Client) aus dem Repo
#    EvickaStudio/lidl-discounts (Apache-2.0, login-frei) und legt sie neben
#    lidl_proxy.py ab, damit `from main import LidlPlus` funktioniert.
#
# Laeuft bei jedem Render-Deploy neu -> der Client ist automatisch aktuell,
# falls das Upstream-Repo Fixes bekommt (Lidl aendert seine Endpunkte ab und an).

set -euo pipefail

LIDL_REPO="https://github.com/EvickaStudio/lidl-discounts.git"
TMP_DIR="$(mktemp -d)"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "==> [1/3] Python-Abhaengigkeiten installieren"
pip install --no-cache-dir -r "$APP_DIR/requirements.txt"

echo "==> [2/3] LidlPlus-Client von ${LIDL_REPO} holen"
git clone --depth 1 "$LIDL_REPO" "$TMP_DIR"

# main.py am Repo-Root + das komplette lidl/-Paket (config.py, models.py, utils.py)
cp "$TMP_DIR/main.py" "$APP_DIR/"
rm -rf "$APP_DIR/lidl"
cp -r "$TMP_DIR/lidl" "$APP_DIR/lidl"

if [ ! -f "$APP_DIR/main.py" ] || [ ! -f "$APP_DIR/lidl/__init__.py" ]; then
  echo "FEHLER: main.py oder das lidl/-Paket wurden nicht gefunden."
  echo "Hat sich die Struktur des Upstream-Repos geaendert? Pruefe:"
  echo "  https://github.com/EvickaStudio/lidl-discounts"
  exit 1
fi

echo "==> [3/3] Build fertig (main.py + lidl/$(ls "$APP_DIR/lidl" | tr '\n' ' '))"
