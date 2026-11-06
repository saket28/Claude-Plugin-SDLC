"""Job folders, events, the usage ledger and locking (INSTRUCTIONS.md §4, §7).

Every job is self-contained in <work_dir>/job-<id>/. Nothing outside a job
folder depends on it: the board is a scan, IDs come from a locked counter, and
the usage ledger keeps totals even after a job folder is deleted.
"""

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        with open(path, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue            # a torn last line never breaks a read
    return out


def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "job"


class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg.work_dir

    # ---- jobs -------------------------------------------------------------
    def job_dir(self, job_id):
        return self.dir / f"job-{job_id}"

    def new_job_id(self):
        counter = self.dir / "counter"
        with locked(counter):
            n = int(counter.read_text().strip() or 0) if counter.exists() else 0
            existing = [int(m.group(1)) for p in self.dir.glob("job-*")
                        if (m := re.fullmatch(r"job-(\d+)", p.name))]
            n = max([n, *existing]) + 1
            write_atomic(counter, f"{n}\n")
        return str(n)

    def create_job(self, title, source=None, job_id=None):
        job_id = job_id or self.new_job_id()
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=False)
        prefix = self.cfg["branch_prefix"]
        branch = f"{prefix}job-{job_id}" if job_id.isdigit() else f"{prefix}{job_id}"
        meta = {
            "id": job_id, "title": title, "source": source or title, "created": now(),
            "branch": branch,
            "worktree": str(self.cfg.worktree_dir / f"job-{job_id}"),
            "pr": None, "owner": os.environ.get("USER", ""), "session": None,
            "released_in": None,
        }
        self.save_job(job_id, meta)
        self.event(job_id, "stage", stage="queued")
        return meta

    def load_job(self, job_id):
        p = self.job_dir(job_id) / "job.json"
        if not p.exists():
            raise KeyError(f"no job {job_id} (looked for {p})")
        return json.loads(p.read_text())

    def save_job(self, job_id, meta):
        p = self.job_dir(job_id) / "job.json"
        with locked(p):
            write_atomic(p, json.dumps(meta, indent=2) + "\n")

    def update_job(self, job_id, **changes):
        p = self.job_dir(job_id) / "job.json"
        with locked(p):
            meta = json.loads(p.read_text())
            meta.update(changes)
            write_atomic(p, json.dumps(meta, indent=2) + "\n")
        return meta

    def jobs(self):
        out = []
        for p in sorted(self.dir.glob("job-*/job.json"), key=_job_sort_key):
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def find_job_by_session(self, session_id):
        if not session_id:
            return None
        for meta in self.jobs():
            s = meta.get("session") or {}
            if s.get("id") == session_id:
                return meta
        return None

    # ---- events -----------------------------------------------------------
    def events(self, job_id):
        return read_jsonl(self.job_dir(job_id) / "events.jsonl")

    def event(self, job_id, type_, **fields):
        rec = {"ts": now(), "type": type_, **fields}
        append_jsonl(self.job_dir(job_id) / "events.jsonl", rec)
        return rec

    # ---- usage ledger -------------------------------------------------------
    def ledger_path(self, month=None):
        month = month or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        return self.dir / "usage" / f"{month}.jsonl"

    def record_usage(self, job_id, key, tokens, **extra):
        append_jsonl(self.ledger_path(), {"ts": now(), "job": job_id, "key": key,
                                          "tokens": tokens, **extra})

    def month_tokens(self, month=None):
        latest = {}
        for r in read_jsonl(self.ledger_path(month)):
            latest[(r.get("job"), r.get("key"))] = r.get("tokens", 0)
        return sum(latest.values())

    # ---- backlog ------------------------------------------------------------
    # backlog.md stays human-editable. One item is a checkbox line followed by
    # optional indented lines: "  @key: value" metadata, anything else is the
    # description. Plain "- [ ] title" lines (the old format) still work.
    def backlog_path(self):
        return self.dir / "backlog.md"

    def _backlog_parse(self):
        """Return (lines, items); each item has its line span [start, end)."""
        p = self.backlog_path()
        lines = p.read_text().splitlines() if p.exists() else []
        items, cur = [], None
        for i, line in enumerate(lines):
            m = re.match(r"^\s*-\s*\[ \]\s+(.*)$", line)
            if m:
                cur = {"title": m.group(1).strip(), "start": i, "end": i + 1,
                       "meta": {}, "desc": []}
                items.append(cur)
            elif cur and (line.startswith("  ") or not line.strip()):
                cur["end"] = i + 1
                mm = re.match(r"^\s+@(\w+):\s*(.*)$", line)
                if mm:
                    cur["meta"][mm.group(1)] = mm.group(2).strip()
                else:
                    cur["desc"].append(line[2:] if line.startswith("  ") else "")
            else:
                cur = None
        for it in items:
            it["description"] = "\n".join(it.pop("desc")).strip()
            it["id"] = it["meta"].get("id") or "b" + hashlib.sha1(it["title"].encode()).hexdigest()[:6]
        return lines, items

    @staticmethod
    def _backlog_render(title, description="", meta=None):
        out = [f"- [ ] {' '.join(title.split())}"]
        out += [f"  @{k}: {' '.join(str(v).split())}" for k, v in (meta or {}).items() if v]
        out += [("  " + l) if l.strip() else "" for l in description.strip().splitlines()]
        return out

    def backlog(self):
        return [it["title"] for it in self._backlog_parse()[1]]

    def backlog_items(self):
        return [{"id": it["id"], "title": it["title"], "description": it["description"],
                 "priority": it["meta"].get("priority", ""), "created": it["meta"].get("created", "")}
                for it in self._backlog_parse()[1]]

    def add_backlog(self, text, description="", priority=""):
        p = self.backlog_path()
        with locked(p):
            cur = p.read_text() if p.exists() else "# Backlog\n\n"
            if not cur.endswith("\n"):
                cur += "\n"
            meta = {"id": "b" + secrets.token_hex(3), "created": now(), "priority": priority}
            lines = self._backlog_render(text, description, meta)
            write_atomic(p, cur + "\n".join(lines) + "\n")
            return meta["id"]

    def update_backlog(self, item_id, title=None, description=None, priority=None):
        p = self.backlog_path()
        with locked(p):
            lines, items = self._backlog_parse()
            it = next((x for x in items if x["id"] == item_id), None)
            if not it:
                raise KeyError(f"no backlog item {item_id}")
            meta = dict(it["meta"])
            meta["id"] = item_id
            if priority is not None:
                meta["priority"] = priority
            new = self._backlog_render(title if title and title.strip() else it["title"],
                                       it["description"] if description is None else description, meta)
            lines[it["start"]:it["end"]] = new
            write_atomic(p, "\n".join(lines) + "\n")

    def delete_backlog(self, item_id):
        p = self.backlog_path()
        with locked(p):
            lines, items = self._backlog_parse()
            it = next((x for x in items if x["id"] == item_id), None)
            if not it:
                raise KeyError(f"no backlog item {item_id}")
            del lines[it["start"]:it["end"]]
            write_atomic(p, "\n".join(lines) + "\n")

    def pop_backlog_item(self, text=None):
        """Remove and return the first item (or the one whose title matches)."""
        p = self.backlog_path()
        with locked(p):
            lines, items = self._backlog_parse()
            for it in items:
                if text is None or it["title"] == text.strip():
                    del lines[it["start"]:it["end"]]
                    write_atomic(p, "\n".join(lines) + "\n")
                    return it
        return None

    def pop_backlog(self, text=None):
        """Remove and return the title of the first unchecked item (or the matching one)."""
        it = self.pop_backlog_item(text)
        return it["title"] if it else None


def _job_sort_key(p):
    name = p.parent.name[4:]
    return (0, int(name), "") if name.isdigit() else (1, 0, name)
