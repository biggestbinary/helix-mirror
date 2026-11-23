#!/usr/bin/env bash
# Ask for access to GSA mirrors that already exist but your github.com account
# cannot see (install.sh/update.sh report these as "no access"). Files an issue
# in $HELIX_MIRROR_ACCESS_REPO when that repository is private, and always
# prints the request and the exact command for a teammate to run.
#
# Usage: ./request-access.sh
source "$(dirname "$0")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) die "unknown option: $1" ;;
esac

check_prerequisites "$GITHUB_DEST_HOST"
python3 "$KIT_DIR/helix_repos.py" request-access --repos-file "$REPOS_FILE"
