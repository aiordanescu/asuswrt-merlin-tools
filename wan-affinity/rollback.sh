#!/bin/sh
set -eu
BASE=${WA_BASE:-/jffs/wan-affinity}
RUN=${WA_RUN:-/tmp/wan-affinity}
export WA_BASE="$BASE" WA_RUN="$RUN"
[ "${1:-}" = --rollback ] || { echo 'usage: rollback.sh --rollback' >&2;exit 2; }
if [ "${WA_INSTALL_LOCK_HELD:-0}" != 1 ]; then
    exec 7>"${WA_INSTALL_LOCK:-/tmp/wan-affinity-install.lock}"
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
hook_error=0
sh "$BASE/hooks.sh" install 7>&- || hook_error=$?
if [ "${WA_ROLLBACK_RESUME:-1}" = 1 ] && ( . "$BASE/wan-affinity.conf"; [ "${ENABLED:-0}" = 1 ]; );then
 sh "$BASE/wan-affinity.sh" start 7>&-
 echo 'Previous version restored; enabled service restarted.'
else echo 'Previous version restored; service remains stopped.';fi
[ "$hook_error" = 0 ] || { echo 'Service restoration completed; startup hooks need attention.' >&2;exit "$hook_error"; }
