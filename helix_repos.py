#!/usr/bin/env python3
"""Discover Helix repositories and plan their private GSA mirrors.

Commands
    discover  list every repository your Helix account can see and append the
              new ones to the repos file with a stable destination name
    plan      check each destination on github.com is private; split specs into "needs a first mirror"
              and "mirrored"; exits 1 if any destination is unsafe. Destinations listed in the
              no-access file are reported as waiting for access instead of being mirrored again,
              and are dropped from that file once they become visible.
    request-access
              record destinations whose name is taken by a repository you cannot see (from
              --conflicts, written by mirror-helix-to-github.sh) and ask for access: open or
              update an issue in $HELIX_MIRROR_ACCESS_REPO when that repository is private,
              and always print what to send a teammate
    grant     (repository admins) give one github.com user access to every private
              <dest-org>/<prefix>* repository you administer, or just the --repo ones

Repos file format (one per line, written by `discover`):
    source-org/repo=falcon-repo     mirrored
    # source-org/repo=falcon-repo   excluded: never mirrored or synced

Destination names are "<prefix><repo>". When the same repository name exists
under more than one Helix owner, every new one gets "<prefix><owner>-<repo>".
Names already in the file never change.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = r"[A-Za-z0-9._-]+"
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
PERMISSIONS = ("pull", "triage", "push", "maintain", "admin")
DEFAULT_ACCESS_REPO = "biggestbinary/helix-mirror"
SPEC_RE = re.compile(rf"^\s*(#\s*)?({NAME})/({NAME})={NAME}\s*$")
FILE_HEADER = """\
# Helix -> github.com/{dest_org} private mirrors, maintained by helix-mirror.
# Comment out a line (put "# " in front) to exclude that repository.
# Do not rename destinations here once mirrored.
"""


def log(message):
    print(message, file=sys.stderr, flush=True)


def run(command, env_host=None):
    env = dict(os.environ)
    if env_host:
        env["GH_HOST"] = env_host
    completed = subprocess.run(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return completed.returncode, completed.stdout, completed.stderr.strip()


def read_specs(path):
    """Return [(src_org, src_repo, dest, excluded)] for every spec line."""
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SPEC_RE.match(line)
        if not match:
            continue
        spec = line.strip().lstrip("#").strip()
        dest = spec.split("=", 1)[1]
        entries.append((match.group(2), match.group(3), dest, bool(match.group(1))))
    return entries


def assign_names(existing, discovered, prefix):
    """Return [(org, repo, dest)] for discovered repos not yet in the file.

    existing:   [(org, repo, dest, excluded)] from the repos file
    discovered: [(org, repo)] from Helix
    """
    known = {(o.lower(), r.lower()) for o, r, _, _ in existing}
    used = {d.lower() for _, _, d, _ in existing}
    owners_by_name = {}
    for org, repo in [(o, r) for o, r, _, _ in existing] + list(discovered):
        owners_by_name.setdefault(repo.lower(), set()).add(org.lower())

    planned = []
    for org, repo in sorted(discovered, key=lambda e: (e[0].lower(), e[1].lower())):
        if (org.lower(), repo.lower()) in known:
            continue
        known.add((org.lower(), repo.lower()))
        if len(owners_by_name[repo.lower()]) > 1:
            base = f"{prefix}{org}-{repo}"
        else:
            base = f"{prefix}{repo}"
        dest, n = base, 2
        while dest.lower() in used:
            dest, n = f"{base}-{n}", n + 1
        used.add(dest.lower())
        planned.append((org, repo, dest))
    return planned


def discover_helix(source_host):
    """Return ([(org, repo)], skipped_empty) for every repo the user can see."""
    code, out, err = run(
        [
            "gh", "api", "--paginate",
            "user/repos?per_page=100&affiliation=owner,collaborator,organization_member",
            "--jq", '.[] | [.owner.login, .name, (.size | tostring)] | @tsv',
        ],
        env_host=source_host,
    )
    if code != 0:
        raise SystemExit(
            f"ERROR: could not list repositories on {source_host}: {err}\n"
            f"Run: gh auth login --hostname {source_host} --git-protocol https"
        )
    repos, empty = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        org, repo, size = parts
        if size == "0":
            empty.append(f"{org}/{repo}")
        else:
            repos.append((org, repo))
    return repos, empty


def append_specs(path, planned, dest_org):
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as handle:
        if new_file:
            handle.write(FILE_HEADER.format(dest_org=dest_org))
        for org, repo, dest in planned:
            handle.write(f"{org}/{repo}={dest}\n")


def check_destination(dest_host, dest_org, dest):
    """Return ("new"|"empty"|"mirrored", None) or (None, problem)."""
    code, out, err = run(
        [
            "gh", "api", f"repos/{dest_org}/{dest}",
            "--jq", "[.full_name, .visibility, (.has_pages|tostring)] | @tsv",
        ],
        env_host=dest_host,
    )
    if code != 0:
        if "Not Found" in err or "404" in err:
            return "new", None
        return None, f"cannot read {dest_org}/{dest}: {err}"
    full_name, visibility, pages = (out.rstrip("\n").split("\t") + [""] * 3)[:3]
    if full_name.lower() != f"{dest_org}/{dest}".lower():
        return None, f"{dest_org}/{dest} redirects to {full_name}"
    if visibility != "private":
        return None, f"{dest_org}/{dest} is {visibility}, NOT private; fix it now"
    if pages != "false":
        return None, f"{dest_org}/{dest} has GitHub Pages enabled"
    code, refs, err = run(
        ["git", "ls-remote", "--heads", "--tags", f"https://{dest_host}/{dest_org}/{dest}.git"]
    )
    if code != 0:
        return None, f"cannot list refs of {dest_org}/{dest}: {err}"
    return ("mirrored" if refs.strip() else "empty"), None


def no_access_path(args):
    if args.no_access_file:
        return Path(args.no_access_file).expanduser()
    return Path(args.repos_file).expanduser().parent / "no-access.txt"


def read_no_access(path):
    """Return {dest.lower(): "org/repo=dest"} from the no-access file."""
    return {dest.lower(): f"{org}/{repo}={dest}" for org, repo, dest, _ in read_specs(path)}


def write_no_access(path, specs):
    if not specs:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Destinations that exist on github.com but your account cannot see.\n"
        "# helix-mirror skips them until access is granted, then drops them from here.\n"
        + "".join(f"{spec}\n" for spec in sorted(specs, key=str.lower)),
        encoding="utf-8",
    )


def gh_login(dest_host):
    code, out, err = run(["gh", "api", "user", "--jq", ".login"], env_host=dest_host)
    if code != 0 or not out.strip():
        raise SystemExit(f"ERROR: cannot read your {dest_host} login: {err}")
    return out.strip()


def access_message(login, dest_org, specs):
    lines = [
        f"ACCESS NEEDED: these {dest_org} repositories already exist, but your github.com",
        f"account ({login}) has no access to them, so you cannot mirror or clone them:",
    ]
    for spec in specs:
        source, dest = spec.split("=", 1)
        lines.append(f"  {dest_org}/{dest}   (Helix {source})")
    repo_args = " ".join(f"--repo {spec.split('=', 1)[1]}" for spec in specs)
    lines += [
        "Ask a teammate with admin rights on them (usually whoever mirrored them first)",
        "to run this from their helix-mirror folder:",
        f"    ./grant-access.sh --user {login} {repo_args}",
        "When access is granted (accept the invitation if GitHub sends one), run ./update.sh;",
        "the repositories are then cloned automatically.",
    ]
    return "\n".join(lines)


def file_access_request(dest_host, access_repo, login, body):
    """Open or update an access-request issue. Return (url, None) or (None, reason)."""
    code, out, err = run(["gh", "api", f"repos/{access_repo}", "--jq", ".visibility"], env_host=dest_host)
    if code != 0:
        return None, f"cannot read {access_repo}: {err}"
    if out.strip() != "private":
        # Repository names are internal; never post them somewhere public.
        return None, f"{access_repo} is not private, so repository names will not be posted there"
    title = f"helix-mirror access request: {login}"
    code, out, err = run(
        [
            "gh", "api", f"repos/{access_repo}/issues?state=open&creator={login}&per_page=100",
            "--jq", f'.[] | select(.title == "{title}") | .number',
        ],
        env_host=dest_host,
    )
    if code != 0:
        return None, f"cannot list issues in {access_repo}: {err}"
    existing = out.split()
    if existing:
        command = ["gh", "api", f"repos/{access_repo}/issues/{existing[0]}/comments", "-f", f"body={body}"]
    else:
        command = ["gh", "api", f"repos/{access_repo}/issues", "-f", f"title={title}", "-f", f"body={body}"]
    code, out, err = run(command + ["--jq", ".html_url"], env_host=dest_host)
    if code != 0:
        return None, f"cannot file the request in {access_repo}: {err}"
    return out.strip(), None


def cmd_request_access(args):
    path = no_access_path(args)
    recorded = read_no_access(path)
    if args.conflicts:
        new = {d.lower(): f"{o}/{r}={d}" for o, r, d, _ in read_specs(Path(args.conflicts))}
        recorded.update(new)
        write_no_access(path, recorded.values())
        specs = sorted(new.values(), key=str.lower)
    else:
        specs = sorted(recorded.values(), key=str.lower)
    if not specs:
        log("Nothing to request: no repositories are waiting for access.")
        return 0
    login = gh_login(args.dest_host)
    message = access_message(login, args.dest_org, specs)
    log(message)
    if args.access_repo:
        url, reason = file_access_request(args.dest_host, args.access_repo, login, message)
        if url:
            log(f"Access request filed: {url}")
        else:
            log(f"Could not file the request automatically ({reason}); send the text above to a teammate.")
    return 0


def list_admin_mirrors(dest_host, dest_org, prefix):
    """Return sorted names of private <dest_org>/<prefix>* repos the caller administers."""
    code, out, err = run(
        [
            "gh", "api", "--paginate",
            "user/repos?per_page=100&affiliation=owner,collaborator,organization_member",
            "--jq", '.[] | [.owner.login, .name, .visibility, (.permissions.admin|tostring)] | @tsv',
        ],
        env_host=dest_host,
    )
    if code != 0:
        raise SystemExit(f"ERROR: could not list your repositories on {dest_host}: {err}")
    names = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        owner, name, visibility, admin = parts
        if (owner.lower() == dest_org.lower() and name.lower().startswith(prefix.lower())
                and visibility == "private" and admin == "true"):
            names.add(name)
    return sorted(names, key=str.lower)


def lookup_user(dest_host, login):
    """Return (login, display) for a github.com user account, or raise SystemExit."""
    if not LOGIN_RE.match(login):
        raise SystemExit(f"ERROR: {login!r} is not a valid github.com username")
    code, out, err = run(
        ["gh", "api", f"users/{login}", "--jq", '[.login, .type, (.name // "")] | @tsv'],
        env_host=dest_host,
    )
    if code != 0:
        if "Not Found" in err or "404" in err:
            raise SystemExit(f"ERROR: there is no {dest_host} user named {login}")
        raise SystemExit(f"ERROR: cannot look up {login}: {err}")
    found, kind, name = (out.rstrip("\n").split("\t") + [""] * 3)[:3]
    if kind != "User":
        raise SystemExit(f"ERROR: {found} is a {kind} account, not a person")
    return found, f"{found} ({name})" if name else found


def add_collaborator(dest_host, dest_org, repo, login, permission):
    """Return ("granted"|"invited", None) or (None, problem)."""
    code, out, err = run(
        ["gh", "api", "-i", "-X", "PUT", f"repos/{dest_org}/{repo}/collaborators/{login}",
         "-f", f"permission={permission}"],
        env_host=dest_host,
    )
    if code != 0:
        problem = err.splitlines()[-1] if err else "request failed"
        if "403" in err:
            problem += " (GSA policy may not allow adding this account; ask a GSA org owner)"
        return None, problem
    status = out.split(None, 2)[1] if len(out.split(None, 2)) > 1 else ""
    return ("invited" if status == "201" else "granted"), None


def cmd_grant(args):
    login = args.user
    if not login:
        if not sys.stdin.isatty():
            raise SystemExit("ERROR: give the account with --user LOGIN")
        login = input("Commercial github.com username to grant access to: ").strip().lstrip("@")
    login, display = lookup_user(args.dest_host, login)
    if login.lower() == gh_login(args.dest_host).lower():
        raise SystemExit("ERROR: that is your own account")

    admin = list_admin_mirrors(args.dest_host, args.dest_org, args.prefix)
    if args.repo:
        by_lower = {name.lower(): name for name in admin}
        repos = []
        for wanted in args.repo:
            wanted = wanted.split("/")[-1]
            if wanted.lower() in by_lower:
                repos.append(by_lower[wanted.lower()])
            else:
                log(f"  skip    {args.dest_org}/{wanted}: not a private {args.prefix}* repository you administer")
    else:
        repos = admin
    if not repos:
        log(f"Nothing to grant: you are not an admin of any matching private {args.dest_org} repository.")
        log("Only a repository's admins (usually whoever mirrored it) or a GSA org owner can grant access.")
        return 1

    log(f"Repositories ({len(repos)}):")
    for name in repos:
        log(f"  {args.dest_org}/{name}")
    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit("ERROR: add --yes to grant without a prompt")
        answer = input(f"Grant {args.permission} access on these {len(repos)} private repositories to {display}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            log("Stopped. Nothing was changed.")
            return 1

    failed = invited = 0
    for name in repos:
        result, problem = add_collaborator(args.dest_host, args.dest_org, name, login, args.permission)
        if problem:
            failed += 1
            log(f"  FAIL    {args.dest_org}/{name}: {problem}")
        else:
            invited += result == "invited"
            log(f"  {result:<7} {args.dest_org}/{name}")
    if invited:
        log(f"{login} must accept {invited} invitation(s) at https://github.com/notifications or by email.")
    log(f"{len(repos) - failed} granted or invited, {failed} failed")
    return 1 if failed else 0


def cmd_discover(args):
    path = Path(args.repos_file).expanduser()
    existing = read_specs(path)
    discovered, empty = discover_helix(args.source_host)
    planned = assign_names(existing, discovered, args.prefix)
    log(f"Helix repositories visible to you: {len(discovered)} (+{len(empty)} empty, skipped)")
    if planned:
        append_specs(path, planned, args.dest_org)
        log(f"New repositories added to {path}: {len(planned)}")
        for org, repo, dest in planned:
            log(f"  {org}/{repo} -> {args.dest_org}/{dest}")
    else:
        log(f"No new repositories; {path} is up to date")
    return 0


def cmd_plan(args):
    path = Path(args.repos_file).expanduser()
    entries = [e for e in read_specs(path) if not e[3]]
    if not entries:
        log(f"ERROR: no active repositories in {path}")
        return 2
    no_access_file = no_access_path(args)
    no_access = read_no_access(no_access_file)
    problems, pending, mirrored, waiting, visible_now = [], [], [], [], []
    for org, repo, dest, _ in entries:
        state, problem = check_destination(args.dest_host, args.dest_org, dest)
        if problem:
            problems.append(problem)
            log(f"  UNSAFE  {org}/{repo} -> {dest}: {problem}")
            continue
        if dest.lower() in no_access:
            if state == "new":
                waiting.append(dest)
                log(f"  no access {org}/{repo} -> {args.dest_org}/{dest} (exists; waiting for access)")
                continue
            visible_now.append(dest.lower())
        if state == "mirrored":
            mirrored.append(f"{org}/{repo}={dest}")
            log(f"  ok      {org}/{repo} -> {args.dest_org}/{dest} (private, mirrored)")
        else:
            pending.append(f"{org}/{repo}={dest}")
            log(f"  to do   {org}/{repo} -> {args.dest_org}/{dest} ({state})")
    if args.pending_out:
        Path(args.pending_out).write_text("".join(f"{s}\n" for s in pending), encoding="utf-8")
    if args.mirrored_out:
        Path(args.mirrored_out).write_text("".join(f"{s}\n" for s in mirrored), encoding="utf-8")
    if visible_now:
        write_no_access(no_access_file, [s for d, s in no_access.items() if d not in visible_now])
        log(f"Access granted since the last run: {len(visible_now)}")
    summary = f"{len(entries)} active, {len(pending)} need a first mirror, {len(problems)} unsafe"
    if waiting:
        summary += f", {len(waiting)} waiting for access"
    log(summary)
    if waiting:
        log("  Ask a teammate who administers them to run ./grant-access.sh for you;")
        log("  ./request-access.sh prints (and, where possible, files) the request again.")
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("discover", "plan", "request-access", "grant"))
    parser.add_argument("--repos-file", default=os.environ.get(
        "HELIX_MIRROR_REPOS", str(Path.home() / ".config" / "helix-mirror" / "repos.txt")))
    parser.add_argument("--no-access-file", help="default: no-access.txt next to the repos file")
    parser.add_argument("--source-host", default=os.environ.get("HELIX_HOST", "github.helix.gsa.gov"))
    parser.add_argument("--dest-host", default=os.environ.get("GITHUB_DEST_HOST", "github.com"))
    parser.add_argument("--dest-org", default=os.environ.get("GITHUB_DEST_ORG", "GSA"))
    parser.add_argument("--prefix", default=os.environ.get("HELIX_MIRROR_PREFIX", "falcon-"))
    parser.add_argument("--pending-out", help="write specs needing a first mirror here")
    parser.add_argument("--mirrored-out", help="write specs already mirrored and private here")
    parser.add_argument("--conflicts", help="request-access: specs whose destination name is taken")
    parser.add_argument("--access-repo", default=os.environ.get("HELIX_MIRROR_ACCESS_REPO", DEFAULT_ACCESS_REPO),
                        help="private github.com repo for access-request issues (empty: never file)")
    parser.add_argument("--user", help="grant: github.com username to add")
    parser.add_argument("--repo", action="append", help="grant: only this repository (repeatable)")
    parser.add_argument("--permission", choices=PERMISSIONS, default="push", help="grant: default push (write)")
    parser.add_argument("--yes", action="store_true", help="grant: do not ask for confirmation")
    args = parser.parse_args(argv)
    commands = {"discover": cmd_discover, "plan": cmd_plan,
                "request-access": cmd_request_access, "grant": cmd_grant}
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
