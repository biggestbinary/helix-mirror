#!/usr/bin/env bash
# First-time setup: mirror every Helix repository you can see into private
# github.com/GSA/falcon-<repo> repositories and clone them under ~/falcon.
# Asks before creating anything. Safe to re-run.
#
# Usage: ./install.sh [--yes]
source "$(dirname "$0")/common.sh"

confirm=1
case "${1:-}" in
    --yes|-y) confirm=0 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) die "unknown option: $1" ;;
esac

check_prerequisites
run_mirror "$confirm"
