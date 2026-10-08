"""配置加载：优先 PyYAML；设备无 PyYAML 时使用内置 YAML 子集解析器。

子集覆盖本项目 config/*.yaml 的全部语法：嵌套映射、行内映射 {}、行内列表 []、
块列表（"- "）、注释、标量（int/float/bool/字符串/引号字符串）。
"""
from __future__ import annotations

import os
from typing import Any


def load_yaml(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return parse_yaml_lite(text)


def load_config_dir(cfg_dir: str) -> dict:
    return {
        "main": load_yaml(os.path.join(cfg_dir, "main.yaml")),
        "rules": load_yaml(os.path.join(cfg_dir, "rules.yaml")),
        "traffic_profiles": load_yaml(os.path.join(cfg_dir, "traffic_profiles.yaml")),
    }


# ---------------------------------------------------------------------------
# YAML 子集解析器（stdlib）
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    out, in_s, in_d = [], False, False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            if i == 0 or line[i - 1] in " \t":
                break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "":
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        return tok[1:-1]
    low = tok.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        return [] if not inner else [_scalar(x) for x in _split_top(inner)]
    if tok.startswith("{") and tok.endswith("}"):
        inner = tok[1:-1].strip()
        out = {}
        for part in _split_top(inner):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            out[k.strip()] = _scalar(v)
        return out
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _split_top(s: str) -> list:
    """按顶层逗号拆分（忽略引号/嵌套括号内的逗号）。"""
    out, buf, depth, in_s, in_d = [], [], 0, False, False
    for ch in s:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif not in_s and not in_d:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def parse_yaml_lite(text: str) -> Any:
    lines = []
    for raw in text.splitlines():
        ln = _strip_comment(raw)
        if not ln.strip():
            continue
        if ln.lstrip().startswith("-"):
            indent = len(ln) - len(ln.lstrip())
            lines.append((indent, ln.strip()))
        else:
            indent = len(ln) - len(ln.lstrip())
            lines.append((indent, ln.strip()))
    pos = [0]

    def parse_block(indent: int):
        if pos[0] >= len(lines):
            return None
        first = lines[pos[0]]
        is_list = first[1].startswith("-")
        out = [] if is_list else {}
        while pos[0] < len(lines):
            ind, content = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent:
                break                      # 交给上层递归（不应发生）
            if content.startswith("-"):
                body = content[1:].strip()
                pos[0] += 1
                if not body:
                    sub = parse_block_next(ind)
                    out.append(sub)
                elif ":" in body and not body.startswith("{") and not body.startswith("["):
                    # "- key: value" 行内映射项（本项目未用，做兼容）
                    k, v = body.split(":", 1)
                    item = {k.strip(): _scalar(v)}
                    if v.strip() == "" and pos[0] < len(lines) and lines[pos[0]][0] > ind:
                        item[k.strip()] = parse_block_next(ind)
                    out.append(item)
                else:
                    out.append(_scalar(body))
            else:
                if ":" not in content:
                    pos[0] += 1
                    continue
                key, val = content.split(":", 1)
                key, val = key.strip(), val.strip()
                pos[0] += 1
                if val == "":
                    if pos[0] < len(lines) and lines[pos[0]][0] > ind:
                        out[key] = parse_block_next(ind)
                    else:
                        out[key] = None
                else:
                    out[key] = _scalar(val)
        return out

    def parse_block_next(parent_indent: int):
        if pos[0] >= len(lines):
            return None
        return parse_block(lines[pos[0]][0])

    result = parse_block(lines[0][0] if lines else 0)
    return result
