# helix-mirror

Copies every Helix repository you can see into **private** repositories named
`github.com/GSA/falcon-<repo>`, then keeps them up to date. You also get a
working clone of each repository under `~/falcon`, with two remotes:
`origin` (the GSA mirror) and `helix` (the original).

Run it on your **AWS WorkSpace**. That machine can reach both Helix and github.com.

## What you need

- `git`, `gh` (GitHub CLI) and `python3`
- A Helix account
- A github.com account that belongs to the GSA org **and is allowed to create
  repositories there**. If you're not sure, ask an org owner.

## Setup (once)

1. Log in to both hosts. Each command opens a browser.

   ```bash
   gh auth login --hostname github.helix.gsa.gov --git-protocol https --web
   gh auth setup-git --hostname github.helix.gsa.gov
   gh auth login --hostname github.com --git-protocol https --web
   gh auth setup-git --hostname github.com
   ```

2. Get the kit. You need read access to this private repository; ask
   biggestbinary to add you.

   ```bash
   GH_HOST=github.com gh repo clone biggestbinary/helix-mirror
   ```

   Later, run `git -C helix-mirror pull` to pick up new versions.

## Install: make the first mirrors

```bash
cd helix-mirror
./install.sh
```

The script lists every repository it found and every GSA repository it is
about to create, then asks `Proceed? [y/N]`. Type `y`.

To leave a repository out, open `~/.config/helix-mirror/repos.txt`, put `# `
in front of its line, and run `./install.sh` again.

## Update: pull in new Helix changes

```bash
./update.sh
```

Run it whenever you want, or every hour from cron:
`0 * * * * /path/to/helix-mirror/update.sh >> ~/helix-mirror.log 2>&1`.

Each run:

- mirrors any new repository that has appeared on Helix
- re-checks that every mirror is still private
- fast-forwards GSA branches and tags to match Helix

Both scripts are safe to run again and again. Work that is already done gets skipped.

## Safety rules

- A mirror is only pushed to after it is confirmed **private**, with no GitHub
  Pages site and with GitHub Actions turned off.
- If a mirror ever stops being private, the scripts stop syncing it and
  report `UNSAFE ... NOT private`. Make it private straight away.
- The scripts never force-push, merge or delete anything. If a branch has
  changed on both Helix and GSA, it is reported as `DIVERGED` and skipped. A
  person needs to decide what to do with it.
- If a repository named `GSA/falcon-<repo>` already exists but is a mirror of
  a different Helix repo, the scripts refuse to use it.
- Any repository with a file over 100 MB is refused. GitHub.com rejects those files.

## Naming

| Helix repository | GSA mirror |
| --- | --- |
| `FSS19-Cloud/supply-chain-user-interface` | `GSA/falcon-supply-chain-user-interface` |
| `FSS19-Cloud/infrastructure` and `other-team/infrastructure` (same name in two orgs) | `GSA/falcon-FSS19-Cloud-infrastructure`, `GSA/falcon-other-team-infrastructure` |

A name is saved in `~/.config/helix-mirror/repos.txt` the first time it is
chosen and never changes after that. If a teammate has already mirrored a
repository, your run sees it as already mirrored and just clones it.

Empty Helix repositories are skipped until someone pushes something to them.

## Settings

Change these with environment variables. You usually won't need to.

| Variable | Default |
| --- | --- |
| `HELIX_MIRROR_INTO` | `~/falcon` (where the clones go) |
| `HELIX_MIRROR_REPOS` | `~/.config/helix-mirror/repos.txt` |
| `HELIX_MIRROR_PREFIX` | `falcon-` |
| `GITHUB_DEST_ORG` | `GSA` |
| `HELIX_HOST` | `github.helix.gsa.gov` |

## Working on a repository

```bash
cd ~/falcon/falcon-supply-chain-user-interface
git switch -c my-change            # open your PR against origin (GSA) as usual
# To send a branch back to Helix and open a PR there:
/path/to/helix-mirror/sync-helix-gsa.py push --repos-file ~/.config/helix-mirror/repos.txt \
    --repo falcon-supply-chain-user-interface --branch my-change --pr
# To check how GSA and Helix differ, without changing anything:
/path/to/helix-mirror/sync-helix-gsa.py status --repos-file ~/.config/helix-mirror/repos.txt -v
```

## Troubleshooting

| Message | Fix |
| --- | --- |
| `not logged in to <host>` | Run the two `gh auth` commands for that host (Setup, step 2). |
| `cannot resolve github.helix.gsa.gov` / `network path ... blocked` | You're not on the WorkSpace, or the VPN is down. |
| `gh repo create failed` | Your github.com account can't create repositories in GSA. Ask an org owner. |
| `blob over 100MB` | That repository needs a large-file cleanup before it can be mirrored. Comment it out for now. |
| `DIVERGED` | The branch changed on both sides. Reconcile it by hand in `~/falcon/<repo>`. |

## Sample run

This is what `./install.sh --yes` printed for three repositories:

```text
==> Finding every repository you can see on github.helix.gsa.gov
Helix repositories visible to you: 3 (+0 empty, skipped)
New repositories added to ~/.config/helix-mirror/repos.txt: 3
  FSS19-Cloud/infrastructure -> GSA/falcon-FSS19-Cloud-infrastructure
  FSS19-Cloud/supply-chain-user-interface -> GSA/falcon-supply-chain-user-interface
  other-team/infrastructure -> GSA/falcon-other-team-infrastructure

==> Checking destinations in github.com/GSA
  to do   FSS19-Cloud/infrastructure -> GSA/falcon-FSS19-Cloud-infrastructure (new)
  ...
==> Mirroring new repositories (private, Actions disabled)
=== FSS19-Cloud/supply-chain-user-interface -> GSA/falcon-supply-chain-user-interface
    cloning https://github.helix.gsa.gov/FSS19-Cloud/supply-chain-user-interface.git
    refs: 1 branch(es), 0 tag(s); on-disk 0 MB; default main
    large-file check: no blob over 100MB
    creating private repo GSA/falcon-supply-chain-user-interface
    verified 1 ref(s) on destination
    default branch set to main
...
==> Updating working clones in ~/falcon and fast-forwarding mirrors from Helix
=== FSS19-Cloud/supply-chain-user-interface -> ~/falcon/falcon-supply-chain-user-interface
    cloning https://github.com/GSA/falcon-supply-chain-user-interface.git
    added remote helix -> https://github.helix.gsa.gov/FSS19-Cloud/supply-chain-user-interface.git
    branches: 1 in-sync
Summary
  OK    falcon-FSS19-Cloud-infrastructure
  OK    falcon-supply-chain-user-interface
  OK    falcon-other-team-infrastructure

Done. Repos file: ~/.config/helix-mirror/repos.txt   Clones: ~/falcon
```

## Files

| File | Purpose |
| --- | --- |
| `install.sh`, `update.sh` | The two commands above |
| `common.sh` | Shared steps behind both commands |
| `helix_repos.py` | Finds Helix repositories, picks names and checks that destinations are safe |
| `mirror-helix-to-github.sh` | Makes the first private mirror of each repository |
| `sync-helix-gsa.py` | Keeps clones and mirrors in sync, and can push branches or open PRs back to Helix |
| `verify-mirror.sh` | Read-only check that a mirror's refs exactly match Helix |

Tests (offline, no network): `python3 -m unittest discover -p 'test_*.py'`.
