"""Agent-context A/B: same ~2600-token prompt, 64 tokens out, twice per backend.

The first call pays cold prefill; the second shows prompt-cache reuse. Prints the
server's own timings so CPU vs Vulkan can be compared at the context size the
harness actually uses (dsh's prefix is ~2.6K tokens).

Usage: python bench_ctx.py <label> [base_url] [model] [prompt_tokens] [max_tokens]
"""
import json
import sys
import urllib.request

label = sys.argv[1] if len(sys.argv) > 1 else "run"
base = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080").rstrip("/")
model = sys.argv[3] if len(sys.argv) > 3 else "qwen3-1.7b"
want_tokens = int(sys.argv[4]) if len(sys.argv) > 4 else 2600
max_tokens = int(sys.argv[5]) if len(sys.argv) > 5 else 64

# ~5 tokens per "tokenNNNN" word in this tokenizer
words = [f"tok{i:04d}" for i in range(want_tokens // 5)]
prompt = ("以下是待统计的词表，请只输出条目数：\n" + " ".join(words) + "\n条目数：")


def call():
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode("utf-8"))


rows = []
for i, phase in enumerate(("cold", "warm"), 1):
    d = call()
    t = d.get("timings", {})
    u = d.get("usage", {})
    row = {
        "label": label,
        "phase": phase,
        "prompt_tok": u.get("prompt_tokens"),
        "prefill_ms": round(t.get("prompt_ms", 0), 1),
        "prefill_tps": round(t.get("prompt_per_second", 0), 1),
        "decode_tok": u.get("completion_tokens"),
        "decode_tps": round(t.get("predicted_per_second", 0), 2),
    }
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

with open(r"C:/AI/logs/bench-ctx.jsonl", "a", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
