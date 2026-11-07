import json
import shutil
import threading
import unittest
import urllib.request
import urllib.error

from helpers import make_repo
from test_pipeline import Agents, drive, BASE_MD
from sdlc import config, serve
from sdlc.store import Store


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo(BASE_MD.format(merge_gate="auto", max_rounds=3, extra=""))
        cls.cfg = config.load(cls.repo)
        cls.job = Store(cls.cfg).create_job("Say hello")["id"]
        drive(cls.cfg, cls.job, Agents())
        serve.Handler.token, serve.Handler.root = "tok", cls.repo
        cls.srv = serve.Server(("127.0.0.1", 0), serve.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        serve.Handler.origin = cls.base
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.repo.parent, ignore_errors=True)

    def req(self, path, body=None, token="tok", origin=None):
        h = {"X-SDLC-Token": token} if token else {}
        if body is not None:
            h["Content-Type"] = "application/json"
        if origin:
            h["Origin"] = origin
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                   headers=h, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r) as resp:
                t = resp.read().decode()
                return resp.status, (json.loads(t) if resp.headers.get_content_type() == "application/json" else t)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_token_required(self):
        self.assertEqual(self.req("/api/board", token=None)[0], 403)
        self.assertEqual(self.req("/api/board", token="wrong")[0], 403)

    def test_board_and_job(self):
        code, b = self.req("/api/board")
        self.assertEqual(code, 200)
        self.assertEqual(b["jobs"][0]["status"], "done")
        code, j = self.req(f"/api/job?id={self.job}")
        self.assertTrue(any(s["stage"] == "review" for s in j["timeline"]))
        by = {x["stage"]: x for x in j["timeline"]}
        self.assertEqual(by["review"]["status"], "PASS")
        self.assertIn("round-1/review-code.md", by["review"]["files"])
        self.assertEqual(j["max_rounds"], 3)
        names = {x["name"]: x for x in by["review"]["detail"]["reviewers"]}
        self.assertEqual(names["code"]["state"], "ran")
        self.assertIn(names["security"]["state"], ("skipped", "not evaluated"))
        self.assertNotIn("rounds", j)
        code, t = self.req(f"/api/file?job={self.job}&path=spec.md")
        self.assertIn("revision", t)

    def test_backlog_card_crud(self):
        st = Store(self.cfg)
        st.add_backlog("Legacy one-liner")
        code, r = self.req("/api/backlog", {"action": "add", "title": "Add CSV export",
                                            "description": "Line one\n\nLine three", "priority": "high"})
        self.assertEqual(code, 200, r)
        items = self.req("/api/board")[1]["backlog_items"]
        it = next(i for i in items if i["id"] == r["id"])
        self.assertEqual((it["description"], it["priority"]), ("Line one\n\nLine three", "high"))
        self.assertIn("Legacy one-liner", [i["title"] for i in items])
        self.assertEqual(self.req("/api/backlog", {"action": "update", "id": r["id"], "title": "CSV",
                                                   "description": "new"})[0], 200)
        it = next(i for i in st.backlog_items() if i["id"] == r["id"])
        self.assertEqual((it["title"], it["description"], it["priority"]), ("CSV", "new", "high"))
        self.assertEqual(self.req("/api/backlog", {"action": "add", "title": " "})[0], 400)
        self.assertEqual(self.req("/api/backlog", {"action": "delete", "id": "nope"})[0], 400)
        self.assertEqual(st.pop_backlog_item("CSV")["description"], "new")
        self.assertEqual(st.pop_backlog(), "Legacy one-liner")

    def test_alerts_stored_in_job_folder_and_cleared(self):
        job = Store(self.cfg).create_job("Alerts")["id"]
        st = Store(self.cfg)
        st.event(job, "needs_human", reason="budget", detail="over")
        st.event(job, "failed", reason="boom")
        st.event(job, "budget", level="warn", scope="job", used=8, limit=10)
        kinds = [a["kind"] for a in self.req(f"/api/job?id={job}")[1]["alerts"]]
        self.assertEqual(kinds, ["needs-human", "failed", "budget-warning"])
        self.assertTrue((st.job_dir(job) / "alerts.json").exists())
        first = self.req(f"/api/job?id={job}")[1]["alerts"][0]["id"]
        self.req("/api/alerts/clear", {"job": job, "ids": [first]})
        self.assertEqual(len(self.req(f"/api/job?id={job}")[1]["alerts"]), 2)
        self.req("/api/alerts/clear", {"job": job})
        self.assertEqual(self.req(f"/api/job?id={job}")[1]["alerts"], [])
        self.assertIn("needs-human", self.req("/api/board")[1]["columns"])

    def test_delete_learning(self):
        sd = self.repo / "SDLC.md"
        sd.write_text(sd.read_text() + "\n## Learnings\n- [L-abcd] builder · Do X (job-1)\n- [L-ef01] all · Keep Y (job-2)\n")
        self.req("/api/learnings/delete", {"id": "L-abcd"})
        self.assertNotIn("L-abcd", sd.read_text())
        self.assertIn("L-ef01", sd.read_text())
        self.assertEqual(self.req("/api/learnings/delete", {"id": "L-abcd"})[0], 400)

    def test_update_learning(self):
        sd = self.repo / "SDLC.md"
        sd.write_text(sd.read_text() + "\n## Learnings\n- [L-abcd] builder · Do X (job-1)\n")
        self.req("/api/learnings/update", {"id": "L-abcd", "roles": ["tester", "builder"], "text": "Do Z"})
        self.assertIn("- [L-abcd] tester, builder · Do Z", sd.read_text())
        self.assertEqual(self.req("/api/learnings/update", {"id": "L-nope", "text": "x"})[0], 400)
        self.assertEqual(self.req("/api/learnings/update", {"id": "L-abcd", "text": " "})[0], 400)

    def test_help(self):
        code, t = self.req("/api/help")
        self.assertEqual(code, 200)
        self.assertIn("/sdlc:review", t)

    def test_no_path_traversal(self):
        self.assertEqual(self.req(f"/api/file?job={self.job}&path=../../SDLC.md")[0], 404)

    def test_settings_preview_save_and_stale_hash(self):
        code, s = self.req("/api/settings")
        self.assertIn("groups", s["schema"])
        code, p = self.req("/api/settings/preview", {"changes": {"max_rounds": 4}})
        self.assertIn("max_rounds: 4", p["after"])
        code, r = self.req("/api/settings", {"changes": {"max_rounds": 4}, "hash": s["hash"]})
        self.assertEqual(code, 200, r)
        self.assertIn("max_rounds: 4", (self.repo / "SDLC.md").read_text())
        code, r = self.req("/api/settings", {"changes": {"max_rounds": 5}, "hash": s["hash"]})
        self.assertEqual(code, 400)                                # stale hash rejected
        self.assertIn("changed on disk", r["error"])

    def test_invalid_settings_rejected(self):
        code, s = self.req("/api/settings")
        code, r = self.req("/api/settings", {"changes": {"gates.merge": "sometimes"}, "hash": s["hash"]})
        self.assertEqual(code, 400)

    def test_reviewer_file_editing(self):
        code, s = self.req("/api/settings")
        self.assertIn("{{name}}", s["agent_template"])
        self.assertIn("## Important", s["reviewer_template"])
        change = {"reviewers.a11y": {"instructions": ".claude/reviewers/a11y.md", "model": "haiku"}}
        code, r = self.req("/api/settings/preview", {"changes": change, "files": {".claude/reviewers/a11y.md": "# a11y\n"}})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["writes"], [{"path": ".claude/reviewers/a11y.md", "new": True}])
        self.assertFalse((self.repo / ".claude/reviewers/a11y.md").exists())          # preview writes nothing
        code, r = self.req("/api/settings", {"changes": change, "hash": s["hash"],
                                              "files": {".claude/reviewers/a11y.md": "# a11y\n"}})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.req("/api/reviewer?name=a11y")[1]["content"], "# a11y\n")
        code, r = self.req("/api/settings/preview", {"changes": {}, "files": {"../x.md": "no"}})
        self.assertEqual(code, 400)
        code, r = self.req("/api/settings/preview", {"changes": {"reviewers.bad": {"run": "true", "model": "gpt"}}})
        self.assertEqual(code, 400)

    def test_cross_origin_refused(self):
        code, _ = self.req("/api/settings", {"changes": {}}, origin="https://evil.example")
        self.assertEqual(code, 403)


class FindingsTest(unittest.TestCase):
    def test_open_findings_parsed_from_reviews(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "review-code.md").write_text(
            "---\nverdict: CHANGES\n---\n\n# Review: code\n\n## Important\n"
            "1. `a.py:1` Bad redirect.\n   Tabs bypass the regex.\n2. `b.py:2` Second.\n\n## Nit\n1. Tiny.\n")
        fs = serve._findings(d)
        self.assertEqual([f["severity"] for f in fs], ["Important", "Important", "Nit"])
        self.assertEqual(fs[0]["why"], "Tabs bypass the regex.")
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
