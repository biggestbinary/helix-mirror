#!/usr/bin/env bash
# Routine refresh, no prompts (cron-friendly): mirrors any Helix repository
# that appeared since the last run, re-checks every mirror is private, and
# fast-forwards GSA branches and tags from Helix. Never force-pushes, merges
# or deletes; branches that moved on both sides are reported and skipped.
#
# Usage: ./update.sh
source "$(dirname "$0")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) die "unknown option: $1" ;;
esac

[ -f "$REPOS_FILE" ] || die "no repos file at $REPOS_FILE; run ./install.sh first"
check_prerequisites
run_mirror 0
