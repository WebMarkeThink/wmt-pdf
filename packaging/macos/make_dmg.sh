#!/bin/sh
# Immagine disco per macOS: wmt-pdf.app e il collegamento ad Applicazioni, da trascinare.
# Uso (su un Mac, dopo PyInstaller): sh packaging/macos/make_dmg.sh 0.1.0 arm64
set -eu

VERSION="${1:?versione}"
ARCH="${2:?architettura (arm64 o x86_64)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HERE/dist/wmt-pdf.app"
OUT="$HERE/dist/wmt-pdf-$VERSION-macos-$ARCH.dmg"
STAGE="$(mktemp -d)"

test -d "$APP" || { echo "manca $APP: prima PyInstaller" >&2; exit 1; }
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "wmt-pdf $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "$OUT"
