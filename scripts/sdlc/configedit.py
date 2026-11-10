"""Edit SDLC.md frontmatter in place (INSTRUCTIONS.md E1).

Only the changed keys are rewritten; comments, ordering and the markdown body are
kept. Values equal to the default are removed, so SDLC.md holds only overrides.
"""

import hashlib
import re

from . import config, yamlish


class EditError(ValueError):
    pass


def file_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _default(path):
    cur = config.DEFAULTS
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return KeyError
        cur = cur[k]
    return cur


def apply(text, changes):
    """changes: {"release.gate.mode": "auto", "global.budget.per_job": 800000, "x": None (= remove)}"""
    fm, body = yamlish.split_frontmatter(text)
    lines = fm.splitlines()
    raw = yamlish.loads(fm) if fm.strip() else {}
    for dotted, value in changes.items():
        path = dotted.split(".")
        if _default(path) == value and path != ["review", "reviewer", "list"]:
            value = None                         # back to default: drop the override
        lines, raw = _set(lines, raw, path, value)
    new_fm = "\n".join(lines).strip("\n")
    yamlish.loads(new_fm)                         # never write something that won't parse
    head = "---\n" + (new_fm + "\n" if new_fm else "") + "---\n"
    return head + ("\n" + body.lstrip("\n") if body.strip() else "")


def _find(lines, key, indent, start=0, end=None):
    end = len(lines) if end is None else end
    rx = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:(.*)$")
    for i in range(start, end):
        m = rx.match(lines[i])
        if m:
            return i, m.group(1)
    return None, None


def _block_end(lines, i, indent):
    j = i + 1
    while j < len(lines):
        s = lines[j]
        if s.strip() and not s.strip().startswith("#") and (len(s) - len(s.lstrip())) <= indent:
            break
        j += 1
    return j


def _comment(rest):
    m = re.search(r"\s+#.*$", rest)
    return m.group(0) if m and rest.count('"') % 2 == 0 else ""


def _set_block3(lines, raw, path, value):
    """Edit `top: / sub: / key:` in place when all three are written as blocks, so comments on the
    neighbouring lines survive. Returns (lines, raw), or None when the layout isn't that simple."""
    top, sub, key = path
    i, rest = _find(lines, top, 0)
    if i is None or (rest.strip() and not rest.strip().startswith("#")):
        return None
    j, jrest = _find(lines, sub, 2, i + 1, _block_end(lines, i, 0))
    if j is None or (jrest.strip() and not jrest.strip().startswith("#")):
        return None
    jend = _block_end(lines, j, 2)
    k, krest = _find(lines, key, 4, j + 1, jend)
    node = raw.setdefault(top, {}).setdefault(sub, {})
    if value is None:
        if k is not None:
            del lines[k:_block_end(lines, k, 4)]
        node.pop(key, None)
        if not node:                                         # the sub block is empty now: drop it, and its parent if empty
            del lines[j:_block_end(lines, j, 2)]
            raw[top].pop(sub, None)
            if not raw[top]:
                del lines[i:_block_end(lines, i, 0)]
                raw.pop(top)
        return lines, raw
    new = f"    {key}: {yamlish.dump_inline(value)}"
    if k is None:
        lines.insert(jend, new)
    else:
        del lines[k + 1:_block_end(lines, k, 4)]
        lines[k] = new + _comment(krest)
    node[key] = value
    return lines, raw


def _set(lines, raw, path, value):
    top = path[0]
    if len(path) == 3:
        done = _set_block3(lines, raw, path, value)
        if done:
            return done
        cur = dict((raw.get(top) or {}).get(path[1]) or {})        # layout isn't plain blocks: rewrite the section
        if value is None:
            cur.pop(path[2], None)
        else:
            cur[path[2]] = value
        return _set(lines, raw, path[:2], cur or None)
    if len(path) > 3:                                              # inside a setting's own map (adapter ops ...)
        cur = dict(((raw.get(top) or {}).get(path[1]) or {}).get(path[2]) or {})
        node = cur
        for k in path[3:-1]:
            node = node.setdefault(k, {})
        if value is None:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = value
        return _set(lines, raw, path[:3], cur or None)
    i, rest = _find(lines, top, 0)
    if len(path) == 1:
        if value is None:
            if i is not None:
                del lines[i:_block_end(lines, i, 0)]
            raw.pop(top, None)
            return lines, raw
        new = f"{top}: {yamlish.dump_inline(value)}"
        if i is None:
            lines.append(new)
        else:
            del lines[i + 1:_block_end(lines, i, 0)]
            lines[i] = new + _comment(rest)
        raw[top] = value
        return lines, raw
    sub = path[1]
    parent = dict(raw.get(top) or {})
    if value is None:
        parent.pop(sub, None)
    else:
        parent[sub] = value
    if i is not None and rest.strip() and not rest.strip().startswith("#"):
        # inline parent: rewrite the whole line
        lines[i] = (f"{top}: {yamlish.dump_inline(parent)}" + _comment(rest)) if parent else ""
        lines = [l for l in lines if l != ""] if not parent else lines
    elif i is not None:
        end = _block_end(lines, i, 0)
        j, crest = _find(lines, sub, 2, i + 1, end)
        if value is None and j is not None:
            del lines[j:_block_end(lines, j, 2)]
        elif j is not None:
            del lines[j + 1:_block_end(lines, j, 2)]
            lines[j] = f"  {sub}: {yamlish.dump_inline(value)}" + _comment(crest)
        elif value is not None:
            lines.insert(end, f"  {sub}: {yamlish.dump_inline(value)}")
    elif value is not None:
        lines.append(f"{top}: {yamlish.dump_inline(parent)}")
    if parent:
        raw[top] = parent
    else:
        raw.pop(top, None)
        if i is not None:
            k, _ = _find(lines, top, 0)
            if k is not None and not any(l.startswith("  ") for l in lines[k + 1:_block_end(lines, k, 0)]):
                del lines[k:_block_end(lines, k, 0)]
    return lines, raw


CONVENTION = re.compile(r"^\.claude/(reviewers|agents)/[A-Za-z0-9][A-Za-z0-9_-]*\.md$")


def _files_to_write(root, files, new_text):
    """Reviewer files edited in the dashboard: {path: content}. Allowed: .claude/reviewers/<name>.md,
    .claude/agents/<name>.md, or the instructions file an existing reviewer already points at."""
    if not files:
        return {}
    raw, _ = yamlish.parse_frontmatter(new_text)
    listed = (((raw.get("review") or {}).get("reviewer")) or {}).get("list")
    known = {r["instructions"] for r in (listed.values() if isinstance(listed, dict) else [])
             if isinstance(r, dict) and isinstance(r.get("instructions"), str)}
    out = {}
    for rel, text in files.items():
        p = (root / rel).resolve()
        if not (CONVENTION.match(rel) or rel in known) or root.resolve() not in p.parents or p.suffix != ".md":
            raise EditError(f"cannot write {rel}: only .claude/reviewers/<name>.md, .claude/agents/<name>.md "
                            "or a reviewer's own instructions file")
        out[rel] = str(text)
    return out


def describe_writes(root, todo):
    return [{"path": p, "new": not (root / p).exists()} for p in sorted(todo)
            if not (root / p).exists() or (root / p).read_text() != todo[p]]


def save(cfg, changes, expected_hash, source="ui", files=None):
    path = cfg.root / "SDLC.md"
    text = path.read_text() if path.exists() else "---\n---\n"
    if expected_hash and file_hash(text) != expected_hash:
        raise EditError("SDLC.md changed on disk since you loaded it; reload and try again")
    new = apply(text, changes)
    todo = _files_to_write(cfg.root, files, new)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(new)
    try:
        _validate_text(cfg.root, new, todo)       # validate before replacing the real file
    except config.ConfigError:
        tmp.unlink()
        raise
    for rel, content in todo.items():
        (cfg.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (cfg.root / rel).write_text(content)
    tmp.replace(path)
    from .store import append_jsonl, now
    append_jsonl(cfg.work_dir / "config-events.jsonl",
                 {"ts": now(), "type": "config_change", "source": source, "keys": sorted(changes)})
    return file_hash(new)


def _validate_text(root, text, assume_files=()):
    raw, body = yamlish.parse_frontmatter(text)
    data, _, _, _ = config.build(root, raw)
    if not data["release"]["host"].get("adapter"):
        data["release"]["host"]["adapter"] = "local"
    config.validate(config.Config(root, data, body, raw), set(assume_files))
