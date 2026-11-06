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
        cls.repo = make_repo(BASE_MD.format(merge_gate="auto", max_rounds=2, extra=""))
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
        self.assertIn("spec.md", j["files"])
        self.assertTrue(any(s["stage"] == "review" for s in j["timeline"]))
        code, t = self.req(f"/api/file?job={self.job}&path=spec.md")
        self.assertIn("revision", t)

    def test_help(self):
        code, t = self.req("/api/help")
        self.assertEqual(code, 200)
        self.assertIn("/sdlc:reviewer", t)

    def test_no_path_traversal(self):
        self.assertEqual(self.req(f"/api/file?job={self.job}&path=../../SDLC.md")[0], 404)

    def test_settings_preview_save_and_stale_hash(self):
        code, s = self.req("/api/settings")
        self.assertIn("groups", s["schema"])
        code, p = self.req("/api/settings/preview", {"changes": {"max_rounds": 3}})
        self.assertIn("max_rounds: 3", p["after"])
        code, r = self.req("/api/settings", {"changes": {"max_rounds": 3}, "hash": s["hash"]})
        self.assertEqual(code, 200, r)
        self.assertIn("max_rounds: 3", (self.repo / "SDLC.md").read_text())
        code, r = self.req("/api/settings", {"changes": {"max_rounds": 4}, "hash": s["hash"]})
        self.assertEqual(code, 400)                                # stale hash rejected
        self.assertIn("changed on disk", r["error"])

    def test_invalid_settings_rejected(self):
        code, s = self.req("/api/settings")
        code, r = self.req("/api/settings", {"changes": {"gates.merge": "sometimes"}, "hash": s["hash"]})
        self.assertEqual(code, 400)

    def test_cross_origin_refused(self):
        code, _ = self.req("/api/settings", {"changes": {}}, origin="https://evil.example")
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()
