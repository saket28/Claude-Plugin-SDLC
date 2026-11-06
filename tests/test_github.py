"""GitHub host adapter against a fake `gh` (no network)."""
import json
import os
import shutil
import unittest
from pathlib import Path

from helpers import make_repo
from sdlc import adapters, config

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
state_f = os.environ["FAKE_GH_STATE"]
st = json.load(open(state_f)) if os.path.exists(state_f) else {"prs": [], "calls": []}
a = sys.argv[1:]
st["calls"].append(a)
out = ""
if a[:2] == ["pr", "list"]:
    out = json.dumps([p for p in st["prs"] if p["head"] == a[a.index("--head") + 1]][:1])
elif a[:2] == ["pr", "create"]:
    st["prs"].append({"number": len(st["prs"]) + 1, "url": "https://github.com/x/y/pull/%d" % (len(st["prs"]) + 1),
                      "state": "OPEN", "head": a[a.index("--head") + 1]})
elif a[:2] == ["pr", "view"]:
    out = json.dumps(st.get("view", {"statusCheckRollup": [], "reviewDecision": "", "mergeStateStatus": "CLEAN",
                                      "state": "OPEN"}))
elif a[:2] == ["pr", "merge"]:
    if st.get("refuse_merge"):
        json.dump(st, open(state_f, "w")); print("Pull request is not mergeable: review required", file=sys.stderr); sys.exit(1)
    st["view"] = {**st.get("view", {}), "state": "MERGED"}
json.dump(st, open(state_f, "w"))
print(out)
'''


class GitHubHostTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("---\nhost: { adapter: github }\n---\n")
        self.addCleanup(shutil.rmtree, self.repo.parent, ignore_errors=True)
        bindir = self.repo.parent / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH)
        (bindir / "gh").chmod(0o755)
        self.state = self.repo.parent / "gh.json"
        os.environ["FAKE_GH_STATE"] = str(self.state)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}:{self.old_path}"
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self.old_path))
        self.host = adapters.host(config.load(self.repo))
        self.body = self.repo.parent / "body.md"
        self.body.write_text("body")

    def gh_state(self):
        return json.loads(self.state.read_text())

    def set_view(self, **v):
        st = self.gh_state() if self.state.exists() else {"prs": [], "calls": []}
        st["view"] = {"statusCheckRollup": [], "reviewDecision": "", "mergeStateStatus": "CLEAN", "state": "OPEN", **v}
        self.state.write_text(json.dumps(st))

    def test_open_pr_is_idempotent(self):
        a = self.host.call("open_pr", branch="sdlc/job-1", base="main", title="t", body_file=str(self.body))
        b = self.host.call("open_pr", branch="sdlc/job-1", base="main", title="t", body_file=str(self.body))
        self.assertEqual(a["pr"], b["pr"])
        self.assertEqual(sum(1 for c in self.gh_state()["calls"] if c[:2] == ["pr", "create"]), 1)

    def test_pr_status_parsing(self):
        self.set_view(statusCheckRollup=[{"conclusion": "SUCCESS"}, {"state": "PENDING"}])
        self.assertEqual(self.host.call("pr_status", branch="b")["ci"], "pending")
        self.set_view(statusCheckRollup=[{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}])
        self.assertEqual(self.host.call("pr_status", branch="b")["ci"], "fail")
        self.set_view(statusCheckRollup=[{"conclusion": "SUCCESS"}], reviewDecision="APPROVED",
                      mergeStateStatus="BEHIND")
        s = self.host.call("pr_status", branch="b")
        self.assertEqual((s["ci"], s["approved"], s["behind"]), ("pass", True, True))
        self.set_view()
        self.assertEqual(self.host.call("pr_status", branch="b")["ci"], "none")

    def test_merge_refused_reports_reason_and_merged_is_idempotent(self):
        self.set_view()
        st = self.gh_state(); st["refuse_merge"] = True; self.state.write_text(json.dumps(st))
        r = self.host.call("merge", branch="b")
        self.assertFalse(r["merged"])
        self.assertIn("review required", r["reason"])
        st = self.gh_state(); st["refuse_merge"] = False; self.state.write_text(json.dumps(st))
        self.assertTrue(self.host.call("merge", branch="b")["merged"])
        self.assertTrue(self.host.call("merge", branch="b")["merged"])     # already merged: no second merge
        self.assertEqual(sum(1 for c in self.gh_state()["calls"] if c[:2] == ["pr", "merge"]), 2)

    def test_command_override_for_one_operation(self):
        repo = make_repo("---\nhost:\n  adapter: github\n  ops:\n"
                         "    merge: \"printf '{\\\"merged\\\": true, \\\"via\\\": \\\"%s\\\"}' {branch}\"\n---\n")
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        h = adapters.host(config.load(repo))
        r = h.call("merge", branch="sdlc/job-9")
        self.assertEqual(r, {"merged": True, "via": "sdlc/job-9"})


if __name__ == "__main__":
    unittest.main()
