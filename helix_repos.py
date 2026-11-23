#!/usr/bin/env python3
"""Discover Helix repositories and plan their private GSA mirrors.

Commands
    discover  list every repository your Helix account can see and append the
              new ones to the repos file with a stable destination name
    plan      check each destination on github.com is private; split specs into "needs a first mirror"
              and "mirrored"; exits 1 if any destination is unsafe

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
    problems, pending, mirrored = [], [], []
    for org, repo, dest, _ in entries:
        state, problem = check_destination(args.dest_host, args.dest_org, dest)
        if problem:
            problems.append(problem)
            log(f"  UNSAFE  {org}/{repo} -> {dest}: {problem}")
            continue
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
    log(f"{len(entries)} active, {len(pending)} need a first mirror, {len(problems)} unsafe")
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("discover", "plan"))
    parser.add_argument("--repos-file", required=True)
    parser.add_argument("--source-host", default=os.environ.get("HELIX_HOST", "github.helix.gsa.gov"))
    parser.add_argument("--dest-host", default=os.environ.get("GITHUB_DEST_HOST", "github.com"))
    parser.add_argument("--dest-org", default=os.environ.get("GITHUB_DEST_ORG", "GSA"))
    parser.add_argument("--prefix", default=os.environ.get("HELIX_MIRROR_PREFIX", "falcon-"))
    parser.add_argument("--pending-out", help="write specs needing a first mirror here")
    parser.add_argument("--mirrored-out", help="write specs already mirrored and private here")
    args = parser.parse_args(argv)
    if args.command == "discover":
        return cmd_discover(args)
    return cmd_plan(args)


if __name__ == "__main__":
    raise SystemExit(main())
