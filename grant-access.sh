#!/usr/bin/env bash
# For repository admins: give a teammate's commercial github.com account
# access to every private github.com/GSA/falcon-* repository you administer
# (or only the --repo ones). Asks for the username if --user is not given,
# shows the list, and asks before changing anything. Accounts outside the GSA
# org get an invitation they must accept.
#
# Usage: ./grant-access.sh [--user LOGIN] [--repo NAME ...]
#                          [--permission pull|triage|push|maintain|admin] [--yes]
#        (default permission: push, i.e. write)
source "$(dirname "$0")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

check_prerequisites "$GITHUB_DEST_HOST"
python3 "$KIT_DIR/helix_repos.py" grant "$@"
