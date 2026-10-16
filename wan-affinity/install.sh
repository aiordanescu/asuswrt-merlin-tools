#!/bin/sh
# Install an extracted static bundle; the example remains disabled by default.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE=/jffs/wan-affinity
die() { echo "wan-affinity install: $*" >&2;exit 1; }
case "${1:-}" in --install|--upgrade) action=$1;; *) die 'usage: install.sh --install|--upgrade';;esac
awk '$1=="Uid:" && $3==0 {ok=1} END{exit !ok}' /proc/self/status || die 'root required'
[ "$(uname -m)" = aarch64 ] || die 'this bundle targets aarch64 Linux'
[ "$(nvram get wans_mode)" = lb ] || die 'dual-WAN load-balance mode required'
for file in wan-affinity wan-affinity.sh hooks.sh wan-affinity.conf.example; do
    [ -s "$SOURCE/$file" ] || die "missing $file"
done
"$SOURCE/wan-affinity" --config "$SOURCE/wan-affinity.conf.example" --check-config
umask 077
exec 7>/tmp/wan-affinity-install.lock
flock -x 7
restore_on_error=0
resume=0
on_exit() {
    rc=$?
    if [ "$rc" -ne 0 ] && [ "$restore_on_error" = 1 ]; then
        echo 'Upgrade failed; restoring the previous version.' >&2
        WA_INSTALL_LOCK_HELD=1 WA_ROLLBACK_RESUME="$resume" sh "$BASE/rollback.sh" --rollback 7>&- || true
    fi
    exit "$rc"
}
trap on_exit 0
if [ "$action" = --install ]; then
    [ ! -e "$BASE" ] || die 'already installed; use --upgrade'
    mkdir -m 700 "$BASE"
    cp "$SOURCE/wan-affinity.conf.example" "$BASE/wan-affinity.conf"
else
    [ -x "$BASE/wan-affinity.sh" ] || die 'no existing installation'
    "$SOURCE/wan-affinity" --config "$BASE/wan-affinity.conf" --check-config
    # One previous version; frequent state is never persisted here.
    rm -rf "$BASE/previous"
    mkdir -m 700 "$BASE/previous"
    for file in wan-affinity wan-affinity.sh hooks.sh wan-affinity.conf; do cp -p "$BASE/$file" "$BASE/previous/$file";done
    if [ -r /tmp/wan-affinity/wan-affinity.pid ]; then
        read pid </tmp/wan-affinity/wan-affinity.pid
        case "$pid" in ''|*[!0-9]*) ;; *)
            if kill -0 "$pid" 2>/dev/null && [ "$(tr '\000' '\n' </proc/"$pid"/cmdline 2>/dev/null | head -n1)" = "$BASE/wan-affinity" ];then resume=1;fi;;esac
    fi
    sh "$BASE/wan-affinity.sh" stop 7>&-
    restore_on_error=1
fi
for file in wan-affinity wan-affinity.sh hooks.sh; do
    cp "$SOURCE/$file" "$BASE/$file.new"
    chmod 700 "$BASE/$file.new"
    mv "$BASE/$file.new" "$BASE/$file"
done
for file in wan-affinity.conf.example rollback.sh README.md BUILDING.md LICENSE; do
    [ ! -f "$SOURCE/$file" ] || cp "$SOURCE/$file" "$BASE/$file"
done
[ ! -d "$SOURCE/docs" ] || { mkdir -p "$BASE/docs"; cp "$SOURCE/docs/"*.md "$BASE/docs/"; }
[ ! -d "$SOURCE/LICENSES" ] || { mkdir -p "$BASE/LICENSES"; cp "$SOURCE/LICENSES/"* "$BASE/LICENSES/"; }
sh "$BASE/hooks.sh" install 7>&-
if [ "$action" = --upgrade ]; then
    if [ "$resume" = 1 ] && grep -q '^ENABLED=1$' "$BASE/wan-affinity.conf"; then
        sh "$BASE/wan-affinity.sh" start 7>&-
    fi
fi
restore_on_error=0
echo 'Installed. Edit /jffs/wan-affinity/wan-affinity.conf, then run /jffs/wan-affinity/wan-affinity.sh start.'
