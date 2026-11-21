#!/usr/bin/env python3
"""Tests for helix_repos.py and an offline end-to-end run of install/update.

The end-to-end test points both hosts at local bare repositories with git
insteadOf rewrites and puts a small fake `gh` on PATH, so real git and the
real scripts run without any network.
"""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("helix_repos", KIT / "helix_repos.py")
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)


class ReadSpecsTests(unittest.TestCase):
    def test_active_excluded_and_other_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repos.txt"
            path.write_text(
                "# Helix -> github.com/GSA private mirrors\n"
                "A/app=falcon-app\n"
                "# B/old=falcon-old\n"
                "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                hr.read_specs(path),
                [("A", "app", "falcon-app", False), ("B", "old", "falcon-old", True)],
            )


class AssignNamesTests(unittest.TestCase):
    def test_prefix_and_collision_across_owners(self):
        planned = hr.assign_names([], [("A", "app"), ("A", "infra"), ("B", "infra")], "falcon-")
        self.assertEqual(
            planned,
            [("A", "app", "falcon-app"), ("A", "infra", "falcon-A-infra"), ("B", "infra", "falcon-B-infra")],
        )

    def test_existing_names_are_stable_and_excluded_never_return(self):
        existing = [("A", "infra", "falcon-infra", False), ("A", "skip", "falcon-skip", True)]
        planned = hr.assign_names(existing, [("A", "infra"), ("A", "skip"), ("B", "infra")], "falcon-")
        self.assertEqual(planned, [("B", "infra", "falcon-B-infra")])

    def test_taken_name_gets_suffix(self):
        existing = [("X", "y", "falcon-app", False)]
        self.assertEqual(hr.assign_names(existing, [("A", "app")], "falcon-"), [("A", "app", "falcon-app-2")])


class CheckDestinationTests(unittest.TestCase):
    def check(self, api_result, refs="abc\trefs/heads/main\n"):
        responses = [api_result, (0, refs, "")]
        with patch.object(hr, "run", side_effect=lambda *a, **k: responses.pop(0)):
            return hr.check_destination("github.com", "GSA", "falcon-app")

    def test_missing_is_new(self):
        self.assertEqual(self.check((1, "", "gh: Not Found (HTTP 404)")), ("new", None))

    def test_private_mirror(self):
        row = "GSA/falcon-app\tprivate\tfalse\n"
        self.assertEqual(self.check((0, row, "")), ("mirrored", None))
        self.assertEqual(self.check((0, row, ""), refs=""), ("empty", None))

    def test_unsafe_destinations(self):
        for row, word in (
            ("GSA/falcon-app\tinternal\tfalse\n", "NOT private"),
            ("GSA/falcon-app\tprivate\ttrue\n", "Pages"),
            ("GSA/other\tprivate\tfalse\n", "redirects"),
        ):
            state, problem = self.check((0, row, ""))
            self.assertIsNone(state)
            self.assertIn(word, problem)


FAKE_GH = r'''#!/usr/bin/env python3
import os, subprocess, sys
root = os.environ["FAKE_ROOT"]
host = os.environ.get("GH_HOST", "github.com")
a = sys.argv[1:]
def bare(name): return os.path.join(root, "gsa", "GSA", name + ".git")
if a[:2] == ["auth", "status"]: sys.exit(0)
if a[:2] == ["repo", "view"]: sys.exit(0 if os.path.isdir(bare(a[2].split("/")[1])) else 1)
if a[:2] == ["repo", "create"]:
    path = bare(a[2].split("/")[1])
    if "--description" in a: sys.exit("fake gh: descriptions must stay blank")
    subprocess.run(["git", "init", "-q", "--bare", path], check=True)
    sys.exit(0)
if a[:2] == ["repo", "edit"]: sys.exit(0)
if a[0] != "api": sys.exit("fake gh: unsupported " + " ".join(a))
a = [x for x in a[1:] if x != "--paginate"]
if a[:2] == ["-X", "PUT"]: sys.exit(0)
path = a[0]
if path.startswith("user/repos"):
    print(open(os.path.join(root, "helix-listing.tsv")).read(), end=""); sys.exit(0)
if path == "user": print("tester"); sys.exit(0)
if path.startswith("orgs/"): print("GSA"); sys.exit(0)
if path.startswith("repos/"):
    name = path.split("/")[2]
    if not os.path.isdir(bare(name)): sys.exit("gh: Not Found (HTTP 404)")
    vis_file = os.path.join(bare(name), "fake-visibility")
    vis = open(vis_file).read().strip() if os.path.exists(vis_file) else "private"
    jq = a[a.index("--jq") + 1]
    if "join" in jq: print(f"GSA/{name} {vis} {str(vis == 'private').lower()} false")
    else: print(f"GSA/{name}\t{vis}\tfalse")
    sys.exit(0)
sys.exit("fake gh: unsupported api " + path)
'''


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        gh = self.root / "bin" / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        (self.root / "gitconfig").write_text(
            textwrap.dedent(f"""\
                [user]
                    name = Test
                    email = test@example.com
                [init]
                    defaultBranch = main
                [protocol "file"]
                    allow = always
                [url "file://{self.root}/helix/"]
                    insteadOf = https://localhost/
                [url "file://{self.root}/gsa/"]
                    insteadOf = https://github.com/
                """),
            encoding="utf-8",
        )
        base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env = dict(
            base,
            PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
            HOME=str(self.root / "home"),
            FAKE_ROOT=str(self.root),
            GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"),
            GIT_CONFIG_NOSYSTEM="1",
            HELIX_HOST="localhost",
        )
        self.listing = []

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        ).stdout.strip()

    def helix_repo(self, org, name, commits=1):
        bare = self.root / "helix" / org / f"{name}.git"
        work = self.root / "work" / org / name
        if not bare.exists():
            self.git("init", "-q", "--bare", str(bare))
            self.git("clone", "-q", str(bare), str(work))
            self.listing.append(f"{org}\t{name}\t10")
        for i in range(commits):
            (work / "f.txt").write_text(f"{name} {i} {os.urandom(4).hex()}\n")
            self.git("add", ".", cwd=work)
            self.git("commit", "-qm", f"c{i}", cwd=work)
        self.git("push", "-q", "origin", "HEAD:main", cwd=work)
        (self.root / "helix-listing.tsv").write_text("\n".join(self.listing) + "\n")
        return work

    def run_kit(self, script, *args):
        return subprocess.run(
            [str(KIT / script), *args], env=self.env, cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    def gsa_head(self, name):
        return self.git("--git-dir", str(self.root / "gsa" / "GSA" / f"{name}.git"), "rev-parse", "main")

    def test_install_then_update(self):
        app = self.helix_repo("orgA", "app")
        self.helix_repo("orgA", "infra")
        self.helix_repo("orgB", "infra")
        self.listing.append("orgA\tempty\t0")
        (self.root / "helix-listing.tsv").write_text("\n".join(self.listing) + "\n")

        result = self.run_kit("install.sh", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout)
        repos_file = self.root / "home" / ".config" / "helix-mirror" / "repos.txt"
        self.assertEqual(
            [line for line in repos_file.read_text().splitlines() if not line.startswith("#")],
            ["orgA/app=falcon-app", "orgA/infra=falcon-orgA-infra", "orgB/infra=falcon-orgB-infra"],
        )
        clone = self.root / "home" / "falcon" / "falcon-app"
        self.assertEqual(self.git("config", "remote.helix.url", cwd=clone), "https://localhost/orgA/app.git")
        self.assertEqual(self.gsa_head("falcon-app"), self.git("rev-parse", "HEAD", cwd=app))

        # New commits upstream, a new repo, and one excluded repo.
        self.helix_repo("orgA", "app", commits=2)
        self.helix_repo("orgC", "new")
        text = repos_file.read_text().replace("orgB/infra=", "# orgB/infra=")
        repos_file.write_text(text)
        result = self.run_kit("update.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.gsa_head("falcon-app"), self.git("rev-parse", "HEAD", cwd=app))
        self.assertTrue((self.root / "gsa" / "GSA" / "falcon-new.git").is_dir())
        self.assertIn("# orgB/infra=falcon-orgB-infra", repos_file.read_text())

        # A mirror that stopped being private is refused and reported.
        (self.root / "gsa" / "GSA" / "falcon-app.git" / "fake-visibility").write_text("public")
        result = self.run_kit("update.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NOT private", result.stdout)


if __name__ == "__main__":
    unittest.main()
