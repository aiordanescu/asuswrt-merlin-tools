#!/bin/sh
# Add/remove only our marked, asynchronous hook block. Preserve other add-ons.
set -eu
SCRIPTS=${WA_SCRIPTS:-/jffs/scripts}
BASE=${WA_BASE:-/jffs/wan-affinity}
case "$BASE:$SCRIPTS" in *[!A-Za-z0-9_./:-]*) echo 'unsafe hook path' >&2; exit 1;; esac
case "${1:-}" in install|remove) action=$1;; *) echo 'usage: hooks.sh install|remove' >&2;exit 2;;esac
umask 077
mkdir -p "$SCRIPTS"
exec 8>"${SCRIPTS}/.wan-affinity-hooks.lock"
flock -x 8
for name in services-start firewall-start; do
    file="$SCRIPTS/$name"
    [ ! -L "$file" ] || { echo "symlink hook: $file" >&2;exit 1; }
    [ -f "$file" ] || [ "$action" = install ] || continue
    if [ -f "$file" ]; then cp -p "$file" "$file.wa-original"; else printf '#!/bin/sh\n' >"$file.wa-original"; fi
    # An unmatched marker indicates an edited block: refuse to discard content.
    awk '
      /^# BEGIN wan-affinity$/ {if(inside)exit 1;inside=1;next}
      /^# END wan-affinity$/ {if(!inside)exit 1;inside=0;next}
      !inside {print}
      END {if(inside)exit 1}' "$file.wa-original" >"$file.wa-clean" || exit 1
    cp -p "$file.wa-original" "$file.wa-new"
    if [ "$action" = install ]; then
        {
            printf '#!/bin/sh\n# BEGIN wan-affinity\n'
            printf 'if [ -x %s/wan-affinity.sh ]; then\n' "$BASE"
            printf '    %s/wan-affinity.sh reconcile >/dev/null 2>&1 &\n' "$BASE"
            printf 'fi\n# END wan-affinity\n'
            # Keep the original shebang only once.
            sed '1{/^#!/d;}' "$file.wa-clean"
        } >"$file.wa-new"
    else cat "$file.wa-clean" >"$file.wa-new"; fi
    sh -n "$file.wa-new"
    if [ -f "$file" ]; then
        cmp -s "$file" "$file.wa-original" || { echo "hook changed concurrently: $file" >&2;exit 1; }
    else chmod 700 "$file.wa-new"; fi
    mv "$file.wa-new" "$file"
    rm -f "$file.wa-clean" "$file.wa-original"
done
