# Shared by install.sh and update.sh. Not meant to be run directly.
# Settings (environment overrides):
#   HELIX_HOST           default github.helix.gsa.gov
#   GITHUB_DEST_ORG      default GSA
#   HELIX_MIRROR_PREFIX  default falcon-
#   HELIX_MIRROR_INTO    where working clones live (default ~/falcon)
#   HELIX_MIRROR_REPOS   repos file (default ~/.config/helix-mirror/repos.txt)
#   HELIX_MIRROR_ACCESS_REPO  private github.com repo for access-request issues

set -o errexit
set -o nounset
set -o pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HELIX_HOST="${HELIX_HOST:-github.helix.gsa.gov}"
export GITHUB_DEST_HOST="github.com"
export GITHUB_DEST_ORG="${GITHUB_DEST_ORG:-GSA}"
export HELIX_MIRROR_PREFIX="${HELIX_MIRROR_PREFIX:-falcon-}"
INTO="${HELIX_MIRROR_INTO:-$HOME/falcon}"
REPOS_FILE="${HELIX_MIRROR_REPOS:-$HOME/.config/helix-mirror/repos.txt}"
export HELIX_MIRROR_REPOS="$REPOS_FILE"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

# check_prerequisites [HOST ...]   (default: Helix and github.com)
check_prerequisites() {
    local tool
    for tool in git gh python3; do
        command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed"
    done
    local host
    [ "$#" -gt 0 ] || set -- "$HELIX_HOST" "$GITHUB_DEST_HOST"
    for host in "$@"; do
        if ! gh auth status --hostname "$host" >/dev/null 2>&1; then
            die "not logged in to $host. Run:
    gh auth login --hostname $host --git-protocol https --web
    gh auth setup-git --hostname $host
then re-run this script."
        fi
    done
}

# run_mirror CONFIRM(0|1)
run_mirror() {
    local confirm="$1" status=0 tmp
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/helix-mirror-plan.XXXXXX")"
    trap 'rm -rf "$tmp"' RETURN

    say "Finding every repository you can see on $HELIX_HOST"
    python3 "$KIT_DIR/helix_repos.py" discover --repos-file "$REPOS_FILE"

    say "Checking destinations in github.com/$GITHUB_DEST_ORG"
    python3 "$KIT_DIR/helix_repos.py" plan --repos-file "$REPOS_FILE" \
        --pending-out "$tmp/pending.txt" || status=1

    if [ -s "$tmp/pending.txt" ]; then
        if [ "$confirm" -eq 1 ]; then
            printf '\nAbout to create %s PRIVATE repo(s) in github.com/%s (list above, "to do").\n' \
                "$(wc -l <"$tmp/pending.txt" | tr -d ' ')" "$GITHUB_DEST_ORG"
            printf 'To skip any, comment its line out in %s and re-run.\n' "$REPOS_FILE"
            read -r -p 'Proceed? [y/N] ' answer || answer=""
            case "$answer" in
                y|Y|yes|YES) ;;
                *) echo "Stopped. Nothing was created."; return 1 ;;
            esac
        fi
        say "Mirroring new repositories (private, Actions disabled)"
        "$KIT_DIR/mirror-helix-to-github.sh" --dest-org "$GITHUB_DEST_ORG" \
            --repos-file "$tmp/pending.txt" --conflicts-out "$tmp/conflicts.txt" || status=1
        if [ -s "$tmp/conflicts.txt" ]; then
            say "Requesting access to repositories that already exist in github.com/$GITHUB_DEST_ORG"
            python3 "$KIT_DIR/helix_repos.py" request-access --repos-file "$REPOS_FILE" \
                --conflicts "$tmp/conflicts.txt" || status=1
        fi
    fi

    say "Re-checking privacy of every mirror"
    python3 "$KIT_DIR/helix_repos.py" plan --repos-file "$REPOS_FILE" \
        --mirrored-out "$tmp/mirrored.txt" >/dev/null 2>"$tmp/plan.log" || {
            status=1; grep UNSAFE "$tmp/plan.log" >&2 || true; }

    if [ -s "$tmp/mirrored.txt" ]; then
        say "Updating working clones in $INTO and fast-forwarding mirrors from Helix"
        python3 "$KIT_DIR/sync-helix-gsa.py" pull --repos-file "$tmp/mirrored.txt" \
            --dest-org "$GITHUB_DEST_ORG" --into "$INTO" || status=1
    fi

    echo
    if [ "$status" -eq 0 ]; then
        echo "Done. Repos file: $REPOS_FILE   Clones: $INTO"
    else
        echo "Finished with problems (see messages above). Fix them and re-run; finished work is skipped."
    fi
    return "$status"
}
