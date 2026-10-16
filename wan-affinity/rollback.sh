#!/bin/sh
set -eu
BASE=/jffs/wan-affinity
[ "${1:-}" = --rollback ] || { echo 'usage: rollback.sh --rollback' >&2;exit 2; }
if [ "${WA_INSTALL_LOCK_HELD:-0}" != 1 ]; then
    exec 7>/tmp/wan-affinity-install.lock
    flock -x 7
fi
for file in wan-affinity wan-affinity.sh hooks.sh wan-affinity.conf; do
    [ -f "$BASE/previous/$file" ] || { echo 'No complete previous version; use wan-affinity.sh disable for firmware fallback.' >&2;exit 1; }
done
sh "$BASE/wan-affinity.sh" stop 7>&-
for file in wan-affinity wan-affinity.sh hooks.sh wan-affinity.conf; do
    cp -p "$BASE/previous/$file" "$BASE/$file.new"
    mv "$BASE/$file.new" "$BASE/$file"
done
sh "$BASE/hooks.sh" install 7>&-
if [ "${WA_ROLLBACK_RESUME:-1}" = 1 ] && grep -q '^ENABLED=1$' "$BASE/wan-affinity.conf"; then sh "$BASE/wan-affinity.sh" start 7>&-; fi
