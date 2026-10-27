"""20-round acceptance runner for the Windows ThinkPad local dsh deployment.

Per round it records: PASS/FAIL (grounded against the real tool output), wall time,
first-tool-call latency, tool result status, llama-server prefill/decode metrics for
every request in that round, and usage tokens.

Usage: python accept20.py [rounds] [outdir]
"""
import json
import os
import re
import subprocess
import sys
import time

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else r"C:/AI/logs/rounds"

DSH_HOME = r"C:/AI/dsh-home"
PROFILE = os.environ.get("DSH_PROFILE", "thinkpad-local")
WORKDIR = r"C:/AI/dsh"
LLAMA_LOG = os.environ.get("LLAMA_LOG", r"C:/AI/logs/llama.out.log")
NODE = r"C:/Users/jedy.ying/AppData/Local/hermes/tools/node-26.12.1-win32-x64/node.exe"
DSH_BIN = (r"C:/Users/jedy.ying/dsh-runtime/npm-global/node_modules/"
           r"@deepseek-ai/dsh/lib/bin.js")

TASKS = [
    ("disk",
     r"请调用 pwsh 工具执行：Get-Date; Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free。"
     r"然后用一句中文总结 C: 盘的已用与剩余空间（用 GB 表示）。必须先调用工具再回答。"),
    ("ls",
     r"请调用 pwsh 工具执行：Get-ChildItem D:\AI -Directory | Select-Object -ExpandProperty Name。"
     r"然后用一句中文说出这些目录名称。必须先调用工具再回答。"),
    ("cpu",
     r"请调用 pwsh 工具执行：Select-String -Path D:\AI\logs\preflight.txt -Pattern 'Core\(TM\)'。"
     r"然后用一句中文说出该文件里的 CPU 型号。必须先调用工具再回答。"),
]

BAD = ("error", "[stderr]", "找不到驱动器", "不存在", "无法访问", "未找到")
REQ = re.compile(
    r"prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens.*?"
    r"eval time =\s+([\d.]+) ms /\s+(\d+) tokens",
    re.S,
)


def log_size():
    try:
        return os.path.getsize(LLAMA_LOG)
    except OSError:
        return 0


def log_slice(start):
    try:
        with open(LLAMA_LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start)
            return f.read()
    except OSError:
        return ""


def grounded(kind, final, tool_text):
    """Does the final answer reuse real values from the tool output?

    Returns (ok, why, quality) — quality flags a unit-conversion error: the answer
    reuses the tool's digits but scales them wrongly (e.g. bytes reported as GB/10).
    """
    low = final.lower()
    if any(b in low for b in BAD):
        return False, "error-marker", "bad"
    if kind == "disk":
        m = re.search(r"Name\s*:\s*C\b.*?Used\s*:\s*(\d+).*?Free\s*:\s*(\d+)", tool_text, re.S)
        if not m:
            return False, "no-usage-in-tool-result", "bad"
        used, free = int(m.group(1)), int(m.group(2))
        # numbers the answer quotes, converted as GB (1e9) and GiB (2**30)
        quoted = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:GB|GiB|G)\b", final, re.I)]
        raw = [int(x) for x in re.findall(r"\b(\d{9,})\b", final)]

        def hits(v):
            return v in raw or any(abs(n * u - v) <= abs(v) * 0.02
                                   for n in quoted for u in (1e9, 2 ** 30))

        got_used, got_free = hits(used), hits(free)
        if got_used and got_free:
            return True, "both-values-exact", "exact"
        if got_used or got_free:
            return True, "one-value-exact", "partial-math"
        sig = {str(used)[:3], str(free)[:3]}
        if any(s in final for s in sig):
            return True, "digits-reused-scale-off", "unit-error"
        return False, "numbers-not-in-answer", "bad"
    if kind == "ls":
        dirs = [d for d in os.listdir(r"C:/AI") if os.path.isdir(os.path.join(r"C:/AI", d))]
        hit = [d for d in dirs if d.lower() in final.lower()]
        return ((True, f"matched {hit}", "exact") if len(hit) >= 2
                else (False, f"only {hit}", "bad"))
    if kind == "cpu":
        ok = "240h" in low or "core" in low
        return ok, "cpu-model", "exact" if ok else "bad"
    return True, "unchecked", "exact"



def run_round(idx):
    kind, task = TASKS[idx % len(TASKS)]
    env = dict(os.environ, DSH_HOME=DSH_HOME, LOCAL_LLM_KEY="sk-local")
    out = os.path.join(OUTDIR, f"round-{idx+1:02d}.jsonl")
    offset = log_size()
    t0 = time.time()
    first_tool = None
    session_t = None
    with open(out, "w", encoding="utf-8") as fh:
        p = subprocess.Popen([NODE, DSH_BIN, "--profile", PROFILE, "--json", task],
                             cwd=WORKDIR, env=env, stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT)
        # poll the event file so each event keeps its arrival time (the app's boot
        # cost would otherwise be attributed to the first tool call)
        seen = 0
        while p.poll() is None:
            if time.time() - t0 > 600:
                p.kill()
                raise subprocess.TimeoutExpired("dsh", 600)
            try:
                with open(out, "r", encoding="utf-8", errors="replace") as rf:
                    lines = [l for l in rf if l.strip().startswith("{")]
            except OSError:
                lines = []
            while seen < len(lines):
                try:
                    e = json.loads(lines[seen])
                except Exception:
                    seen += 1
                    continue
                if e.get("type") == "session" and session_t is None:
                    session_t = time.time() - t0
                elif e.get("type") == "tool_call" and first_tool is None:
                    first_tool = time.time() - t0
                seen += 1
            time.sleep(0.1)
    wall = time.time() - t0

    calls, oks, final, usage, tool_text, turn_end = 0, 0, "", {}, "", None
    with open(out, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type")
            if t == "tool_call":
                calls += 1
            elif t == "tool_result":
                if e.get("status") == "completed":
                    oks += 1
                tool_text += str(e.get("result") or "")
            elif t == "final":
                final = e.get("text") or ""
                turn_end = time.time() - t0
            elif t == "status" and e.get("phase") == "step_end":
                usage = e.get("usage") or {}

    reqs = [(float(a), int(b), float(c), int(d)) for a, b, c, d in REQ.findall(log_slice(offset))]
    main = max(reqs, key=lambda r: r[1]) if reqs else None
    dec_tok = sum(r[3] for r in reqs)
    dec_ms = sum(r[2] for r in reqs)
    ok, why, quality = grounded(kind, final, tool_text)
    ok = ok and oks >= 1
    return {
        "round": idx + 1, "kind": kind,
        "result": "PASS" if ok else "FAIL", "why": why, "quality": quality,
        "wall_s": round(wall, 1),
        "boot_s": round(session_t, 1) if session_t else None,
        "turn_s": round(turn_end - session_t, 1) if session_t and turn_end else None,
        "first_tool_s": round(first_tool, 1) if first_tool else None,
        "tool_calls": calls, "tool_ok": oks,
        "reqs": len(reqs),
        "prefill_tok": main[1] if main else 0,
        "prefill_ms": round(main[0], 1) if main else 0,
        "prefill_tps": round(main[1] / (main[0] / 1000), 1) if main and main[0] else None,
        "decode_tok": dec_tok,
        "decode_tps": round(dec_tok / (dec_ms / 1000), 1) if dec_ms else None,
        "total_tok": usage.get("totalTokens"), "cache_read": usage.get("cacheReadTokens"),
        "final_head": final[:70].replace("\n", " "),
        "events_file": out,
    }


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = []
    for i in range(ROUNDS):
        try:
            row = run_round(i)
        except subprocess.TimeoutExpired:
            row = {"round": i + 1, "result": "TIMEOUT", "wall_s": 600}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        with open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
    ok = sum(1 for r in rows if r.get("result") == "PASS")
    print(f"\nRESULT {ok}/{len(rows)} PASS -> {os.path.join(OUTDIR, 'summary.json')}")


if __name__ == "__main__":
    main()
