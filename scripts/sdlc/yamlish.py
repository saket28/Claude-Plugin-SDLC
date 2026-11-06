"""Parser for the restricted YAML subset used in SDLC.md frontmatter and artifacts.

Supported: block maps, block lists ("- item"), inline maps "{a: 1, b: [x, y]}",
inline lists "[a, b]", quoted and plain scalars, ints, floats, booleans, null,
and "#" comments. Not supported (by design): anchors, aliases, tags, multi-line
strings. Anything outside the subset raises YamlError with a line number.
"""

import re


class YamlError(ValueError):
    pass


def split_frontmatter(text):
    """Return (frontmatter_text, body). Frontmatter is empty if absent."""
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    raise YamlError("frontmatter starts with --- but never closes")


def parse_frontmatter(text):
    fm, body = split_frontmatter(text)
    return (loads(fm) if fm.strip() else {}), body


def _strip_comment(line):
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


_NUM = re.compile(r"^-?\d+(_\d+)*$")
_FLOAT = re.compile(r"^-?\d+\.\d+$")


def _scalar(tok):
    tok = tok.strip()
    if tok == "":
        return None
    if tok[0] in "\"'":
        if len(tok) < 2 or tok[-1] != tok[0]:
            raise YamlError(f"unterminated string: {tok}")
        inner = tok[1:-1]
        return inner.replace('\\"', '"') if tok[0] == '"' else inner.replace("''", "'")
    low = tok.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if _NUM.match(tok):
        return int(tok.replace("_", ""))
    if _FLOAT.match(tok):
        return float(tok)
    if tok[0] in "&*!|>":
        raise YamlError(f"unsupported YAML feature: {tok}")
    return tok


def _split_top(s, sep=","):
    """Split s on sep at nesting depth 0, respecting quotes and brackets."""
    parts, depth, quote, cur = [], 0, None, []
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quote or depth:
        raise YamlError(f"unbalanced brackets or quotes: {s}")
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _key_value(s):
    """Split 'key: value' at the first top-level colon followed by space/end."""
    quote, depth = None, 0
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and (i + 1 == len(s) or s[i + 1] in " \t"):
            return _scalar(s[:i]), s[i + 1:].strip()
    return None, None


def _inline(tok):
    tok = tok.strip()
    if tok.startswith("{"):
        if not tok.endswith("}"):
            raise YamlError(f"unterminated inline map: {tok}")
        out = {}
        for part in _split_top(tok[1:-1]):
            k, v = _key_value(part.strip())
            if k is None:
                raise YamlError(f"expected 'key: value' in inline map: {part}")
            out[str(k)] = _inline(v)
        return out
    if tok.startswith("["):
        if not tok.endswith("]"):
            raise YamlError(f"unterminated inline list: {tok}")
        return [_inline(p) for p in _split_top(tok[1:-1])]
    return _scalar(tok)


def loads(text):
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f"line {n}: tabs are not allowed for indentation")
        line = _strip_comment(raw)
        if line.strip():
            rows.append((n, len(line) - len(line.lstrip()), line.strip()))
    if not rows:
        return {}
    value, i = _block(rows, 0, rows[0][1])
    if i != len(rows):
        raise YamlError(f"line {rows[i][0]}: unexpected indentation")
    return value


def _block(rows, i, indent):
    if rows[i][2].startswith("- ") or rows[i][2] == "-":
        return _list(rows, i, indent)
    return _map(rows, i, indent)


def _map(rows, i, indent):
    out = {}
    while i < len(rows) and rows[i][1] == indent:
        n, _, text = rows[i]
        if text.startswith("-"):
            raise YamlError(f"line {n}: list item where a key was expected")
        k, v = _key_value(text)
        if k is None:
            raise YamlError(f"line {n}: expected 'key: value'")
        k = str(k)
        if k in out:
            raise YamlError(f"line {n}: duplicate key '{k}'")
        i += 1
        if v == "":
            if i < len(rows) and rows[i][1] > indent:
                out[k], i = _block(rows, i, rows[i][1])
            elif i < len(rows) and rows[i][1] == indent and rows[i][2].startswith("- "):
                out[k], i = _list(rows, i, indent)
            else:
                out[k] = None
        else:
            out[k] = _inline(v)
    if i < len(rows) and rows[i][1] > indent:
        raise YamlError(f"line {rows[i][0]}: unexpected indentation")
    return out, i


def _list(rows, i, indent):
    out = []
    while i < len(rows) and rows[i][1] == indent and (rows[i][2].startswith("- ") or rows[i][2] == "-"):
        n, _, text = rows[i]
        item = text[1:].strip()
        i += 1
        if item == "":
            if i < len(rows) and rows[i][1] > indent:
                val, i = _block(rows, i, rows[i][1])
                out.append(val)
            else:
                out.append(None)
            continue
        k, v = _key_value(item) if not item.startswith(("{", "[", "\"", "'")) else (None, None)
        if k is not None:
            # "- key: value" starts a map whose further keys are indented by 2
            child = {str(k): _inline(v) if v else None}
            sub = indent + 2
            if v == "" and i < len(rows) and rows[i][1] > sub:
                child[str(k)], i = _block(rows, i, rows[i][1])
            if i < len(rows) and rows[i][1] == sub and not rows[i][2].startswith("- "):
                more, i = _map(rows, i, sub)
                for mk in more:
                    if mk in child:
                        raise YamlError(f"line {n}: duplicate key '{mk}'")
                child.update(more)
            out.append(child)
        else:
            out.append(_inline(item))
    return out, i


def _fmt_scalar(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r"[:#{}\[\],&*!|>'\"%@`]|^\s|\s$|^-", s) or s.lower() in (
            "true", "false", "yes", "no", "null", "~") or _NUM.match(s) or _FLOAT.match(s):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def dump_inline(v):
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k}: {dump_inline(x)}" for k, x in v.items()) + " }" if v else "{}"
    if isinstance(v, list):
        return "[" + ", ".join(dump_inline(x) for x in v) + "]"
    return _fmt_scalar(v)


def dumps(data, indent=0):
    """Dump nested dicts/lists. Small maps and lists of scalars are written inline."""
    pad = " " * indent
    lines = []
    for k, v in data.items():
        if isinstance(v, dict) and v and not _small(v):
            lines.append(f"{pad}{k}:")
            lines.append(dumps(v, indent + 2))
        elif isinstance(v, list) and v and any(isinstance(x, (dict, list)) for x in v):
            lines.append(f"{pad}{k}:")
            for x in v:
                lines.append(f"{pad}  - {dump_inline(x)}")
        else:
            lines.append(f"{pad}{k}: {dump_inline(v)}")
    return "\n".join(lines)


def _small(d):
    return len(d) <= 4 and all(not isinstance(x, (dict, list)) or not x for x in d.values()) and \
        len(dump_inline(d)) <= 80
