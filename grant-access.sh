#!/usr/bin/env bash
# For repository admins: give a teammate's github.com account access to the
# private github.com/GSA/falcon-* mirrors you name with --repo (or, with --all,
# every mirror in your repos file that you administer). The account must be a
# GSA org member unless --allow-outside-collaborator is given. Shows the list
# and asks before changing anything; --yes needs --repo. Existing access is
# never lowered.
#
# Usage: ./grant-access.sh [--user LOGIN] (--repo NAME ... | --all)
#                          [--permission pull|triage|push|maintain|admin]
#                          [--allow-outside-collaborator] [--yes]
#        (default permission: pull, i.e. read)
source "$(dirname "$0")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

check_prerequisites "$GITHUB_DEST_HOST"
python3 "$KIT_DIR/helix_repos.py" grant "$@"
