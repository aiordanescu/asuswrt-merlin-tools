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
            first=$(head -n 1 "$file.wa-clean")
            case "$first" in '#!'*) printf '%s\n' "$first";; *) printf '#!/bin/sh\n';; esac
            printf '# BEGIN wan-affinity\n'
            printf 'if [ -x %s/wan-affinity.sh ]; then\n' "$BASE"
            printf '    %s/wan-affinity.sh reconcile >/dev/null 2>&1 &\n' "$BASE"
            printf 'fi\n# END wan-affinity\n'
            case "$first" in '#!'*) sed '1d' "$file.wa-clean";; *) cat "$file.wa-clean";; esac
        } >"$file.wa-new"
    else cat "$file.wa-clean" >"$file.wa-new"; fi
    # Check with the declared shell, preserving options such as bash -e.
    first=$(head -n 1 "$file.wa-new")
    case "$first" in '#!'*) interpreter=${first#\#!};; *) interpreter=/bin/sh;; esac
    set -- $interpreter
    case "${1##*/}" in sh|ash|dash|bash|ksh|zsh|env) ;; *) echo "unsupported hook interpreter: $first" >&2;exit 1;; esac
    "$@" -n "$file.wa-new"
    if [ -f "$file" ]; then
        cmp -s "$file" "$file.wa-original" || { echo "hook changed concurrently: $file" >&2;exit 1; }
    else chmod 700 "$file.wa-new"; fi
    mv "$file.wa-new" "$file"
    rm -f "$file.wa-clean" "$file.wa-original"
done
