#!/bin/sh
# Stage and validate every hook before changing any of them.
set -eu
SCRIPTS=${WA_SCRIPTS:-/jffs/scripts}
BASE=${WA_BASE:-/jffs/wan-affinity}
LOCK=${WA_HOOK_LOCK:-/tmp/wan-affinity-hooks.lock}
case "$BASE:$SCRIPTS:$LOCK" in *[!A-Za-z0-9_./:-]*|*'..'*) echo 'unsafe hook path' >&2;exit 1;;esac
case "${1:-}" in install|remove|check) action=$1;;*) echo 'usage: hooks.sh install|remove|check' >&2;exit 2;;esac
umask 077
[ ! -L "$SCRIPTS" ] && [ ! -L "$LOCK" ] || exit 1
mkdir -p "$SCRIPTS"
# A stable lock outside the hook directory: never unlink a held flock inode.
exec 8>"$LOCK"
flock -x 8
stage="$SCRIPTS/.wan-affinity-hooks.$$"
mkdir "$stage" || exit 1
trap 'rm -rf "$stage"' 0
trap 'exit 1' HUP INT TERM
for name in services-start firewall-start;do
 file="$SCRIPTS/$name"
 [ ! -L "$file" ] || { echo "symlink hook: $file" >&2;exit 1; }
 [ -f "$file" ] || [ "$action" != remove ] || continue
 if [ -f "$file" ];then cp -p "$file" "$stage/$name.original"
 else printf '#!/bin/sh\n' >"$stage/$name.original";touch "$stage/$name.absent";fi
 if grep -q '^# \(BEGIN\|END\) wan-affinity$' "$stage/$name.original";then
  awk '
    /^# BEGIN wan-affinity$/ {if(inside)exit 1;inside=1;next}
    /^# END wan-affinity$/ {if(!inside)exit 1;inside=0;next}
    !inside {print}
    END {if(inside)exit 1}' "$stage/$name.original" >"$stage/$name.clean"
 else cp -p "$stage/$name.original" "$stage/$name.clean";fi
 cp -p "$stage/$name.original" "$stage/$name.new"
 if [ "$action" != remove ];then
  {
   first=$(head -n 1 "$stage/$name.clean")
   case "$first" in '#!'*) printf '%s\n' "$first";;*) printf '#!/bin/sh\n';;esac
   printf '# BEGIN wan-affinity\nif [ -x %s/wan-affinity.sh ]; then\n' "$BASE"
   printf '    %s/wan-affinity.sh reconcile >/dev/null 2>&1 &\nfi\n# END wan-affinity\n' "$BASE"
   case "$first" in '#!'*) tail -n +2 "$stage/$name.clean";;*) cat "$stage/$name.clean";;esac
  } >"$stage/$name.new"
  first=$(head -n 1 "$stage/$name.new")
  case "$first" in '#!'*) interpreter=${first#\#!};;*) interpreter=/bin/sh;;esac
  set -- $interpreter
  shell=${1##*/}
  if [ "$shell" = env ] || [ "$shell" = busybox ];then shift;shell=${1:-};shell=${shell##*/};fi
  case "$shell" in sh|ash|dash|bash|ksh|zsh) ;;*) echo "unsupported hook interpreter: $first" >&2;exit 1;;esac
  # Rebuild the original argv, retaining interpreter options.
  set -- $interpreter
  "$@" -n "$stage/$name.new"
 else cat "$stage/$name.clean" >"$stage/$name.new";fi
 [ ! -f "$stage/$name.absent" ] || chmod 700 "$stage/$name.new"
done
[ "$action" != check ] || exit 0
# Check all originals again before the first mutation.
for name in services-start firewall-start;do
 [ -f "$stage/$name.new" ] || continue
 file="$SCRIPTS/$name"
 if [ -f "$stage/$name.absent" ];then [ ! -e "$file" ] || exit 1
 else cmp -s "$file" "$stage/$name.original" || { echo "hook changed concurrently: $file" >&2;exit 1; };fi
done
for name in services-start firewall-start;do
 [ ! -f "$stage/$name.new" ] || mv "$stage/$name.new" "$SCRIPTS/$name"
done
# Retire an unused lock left by older versions. The current stable lock stays
# outside the hook directory, including after removal.
if [ -f "$SCRIPTS/.wan-affinity-hooks.lock" ] && [ ! -L "$SCRIPTS/.wan-affinity-hooks.lock" ];then
 exec 6>"$SCRIPTS/.wan-affinity-hooks.lock"
 if flock -n 6;then rm -f "$SCRIPTS/.wan-affinity-hooks.lock";fi
fi
