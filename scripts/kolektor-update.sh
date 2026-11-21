#!/bin/sh
# Aktualizuje kolektor-rynku z repozytorium git: pobiera zmiany, buduje paczkę i instaluje ją.
# Zachowuje /etc/kolektor-rynku/config.ini (--force-confold). Uruchamiaj jako root.
set -eu

REPO_DIR="${KOLEKTOR_REPO:-/opt/kolektor-rynku-src}"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

cd "$REPO_DIR"
git pull --ff-only

VERSJA="$(sed -n 's/^Version: //p' pkg/DEBIAN/control)"
echo "Buduję kolektor-rynku ${VERSJA}"

cp -a pkg "$BUILD_DIR/pkg"
find "$BUILD_DIR/pkg" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
dpkg-deb --build --root-owner-group "$BUILD_DIR/pkg" "$BUILD_DIR/kolektor-rynku_${VERSJA}_all.deb" >/dev/null

apt-get install -y --no-install-recommends python3-matplotlib >/dev/null
DEBIAN_FRONTEND=noninteractive dpkg -i --force-confold "$BUILD_DIR/kolektor-rynku_${VERSJA}_all.deb"
dpkg --configure -a >/dev/null 2>&1 || true

systemctl is-active kolektor-bot kolektor-www
dpkg -l kolektor-rynku | tail -1
