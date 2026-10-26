"""Partial-prefix cache behaviour: the pattern the harness actually produces.

Call 1 sends the base prompt (cold). Call 2 appends one unique line to the same
base (partial prefix match). Call 3 appends a different line. Prints the server's
prompt_ms for each so CPU vs Vulkan can be compared on incremental reuse, which is
what an agent turn does after a tool result.

Usage: python bench_partial.py <label> [base_url] [model]
"""
import json
import sys
import urllib.request

label = sys.argv[1] if len(sys.argv) > 1 else "run"
base = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080").rstrip("/")
model = sys.argv[3] if len(sys.argv) > 3 else "qwen3-1.7b"

words = [f"tok{i:04d}" for i in range(520)]  # ~2600 tokens
base_prompt = "以下是待统计的词表，请只输出条目数：\n" + " ".join(words)


def call(prompt, max_tokens=16):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode("utf-8"))


rows = []
for i, prompt in enumerate([base_prompt,
                            base_prompt + "\n补充说明一：请简短回答。",
                            base_prompt + "\n补充说明二：请简短回答。"]):
    d = call(prompt)
    t, u = d.get("timings", {}), d.get("usage", {})
    row = {"label": label, "call": i + 1,
           "prompt_tok": u.get("prompt_tokens"),
           "prefill_ms": round(t.get("prompt_ms", 0), 1),
           "prefill_tps": round(t.get("prompt_per_second", 0), 1),
           "decode_tps": round(t.get("predicted_per_second", 0), 2)}
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

with open(r"C:/AI/logs/bench-partial.jsonl", "a", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
