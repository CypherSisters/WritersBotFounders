#!/usr/bin/env bash
# Does a Docker app really run here, behind the Bridge? (D132) One real compose app: it pulls two small images, starts
# behind the wall, is checked and removed. Run it first on a computer that will use Docker, from the folder BuildBot is in.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || { echo "Run ./install.sh first."; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "No: Docker is not installed here."; exit 1; }
docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 || { echo "No: Docker is installed, but this account can't reach it, or it isn't running."; exit 1; }
"$PY" -m pip install -q --disable-pip-version-check pytest || exit 1
OUT="$(PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring "$PY" -m pytest -q -p no:cacheprovider tests/test_i2b_docker_live.py 2>&1)"
CODE=$?
printf '%s\n' "$OUT" | tail -5
if [ "$CODE" = "0" ] && ! printf '%s\n' "$OUT" | tail -1 | grep -q skipped; then
    echo "Yes: a Docker app ran here behind the Bridge."
    exit 0
fi
echo "No: a Docker app did not run here. The lines above say why."
exit 1
