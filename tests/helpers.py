import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""Stand-in for `claude -p` in tests: answers by prompt type, never calls a model."""
import json, os, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
mode = os.environ.get("FAKE_REVIEW", "clean")
if prompt.startswith("/code-review") or prompt.startswith("/security-review"):
    findings = [] if mode == "clean" else [{"severity": "important", "file": "app.py", "line": 1,
                                           "summary": "bug", "detail": "fix it"}]
    if mode == "garbled":
        result = "I found some things but forgot the format."
    elif mode == "native":   # what the real /code-review returns in -p: a bare list, no severity
        result = json.dumps([{"file": "app.py", "line": 1, "summary": "real bug: crashes on empty input",
                              "failure_scenario": "x"}, {"file": "app.py", "line": 2,
                              "summary": "nit: naming", "failure_scenario": "y"}])
    else:
        result = "Review done.\n```json\n" + json.dumps({"findings": findings}) + "\n```"
elif prompt.startswith("Classify each code review finding"):
    items = json.loads(prompt.split("Findings:\n", 1)[1])
    result = json.dumps(["nit" if "nit:" in f["summary"] else "important" for f in items])
elif prompt.startswith("Decide for each condition"):
    result = json.dumps({"arch": {"applies": os.environ.get("FAKE_JUDGE", "no") == "yes", "reason": "fake"}})
else:
    result = json.dumps({"entries": [{"kind": "learning", "roles": ["builder"],
                                      "text": "Run the seed script before tests.", "evidence": "fake"}]})
print(json.dumps({"result": result, "is_error": False, "duration_ms": 1000, "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 1, "output_tokens": 1},
                  "modelUsage": {"claude-opus-5-5": {"inputTokens": 100, "outputTokens": 50,
                                 "cacheCreationInputTokens": 10, "cacheReadInputTokens": 500}}}))
'''


def sh(cmd, cwd):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd}: {r.stderr}")
    return r.stdout.strip()


def make_repo(sdlc_md="", files=None):
    """A throwaway git repo with an SDLC.md, on branch main, plus a fake claude CLI."""
    tmp = Path(tempfile.mkdtemp(prefix="sdlc-test-"))
    repo = tmp / "app"
    repo.mkdir()
    sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
    (repo / "SDLC.md").write_text(textwrap.dedent(sdlc_md).lstrip() or "---\n---\n")
    (repo / "app.py").write_text("print('hi')\n")
    (repo / ".gitignore").write_text(".sdlc/\n")
    for name, text in (files or {}).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    sh("git add -A && git commit -qm init", repo)
    fake = tmp / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    os.environ["SDLC_CLAUDE_BIN"] = str(fake)
    return repo
