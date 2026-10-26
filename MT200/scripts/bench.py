"""Fixed-workload benchmark for a llama-server endpoint.

Two requests per run:
  1) prefill  — ~1200 unique tokens in, 8 tokens out  -> prompt eval speed
  2) decode   — short prompt, 256 tokens out         -> generation speed

Prints the server's own timings so CPU / Vulkan / SYCL runs are comparable.
Usage: python bench.py [label] [base_url] [model]
"""
import json
import os
import sys
import urllib.request

label = sys.argv[1] if len(sys.argv) > 1 else "run"
base = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080").rstrip("/")
model = sys.argv[3] if len(sys.argv) > 3 else "qwen3-1.7b"
WORDS = int(os.environ.get("BENCH_WORDS", "1200"))


def post(path, body):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) prefill: deterministic pseudo-random text so the prompt cache cannot help
words = [f"tok{i:04d}" for i in range(WORDS)]
long_prompt = ("请只统计下面词表的条目数，并回答数字。词表：" + " ".join(words)
               + "\n只回复条目数。")
pre = post("/v1/chat/completions", {
    "model": model,
    "messages": [{"role": "user", "content": long_prompt}],
    "max_tokens": 8,
    "temperature": 0,
})
t1 = pre.get("timings", {})
u1 = pre.get("usage", {})

# 2) decode: ask for a long answer, measure generation speed
dec = post("/v1/chat/completions", {
    "model": model,
    "messages": [{"role": "user", "content": "用中文写一段约 250 字的短文，介绍本地大模型在笔记本上离线运行的好处。不要列点。"}],
    "max_tokens": 256,
    "temperature": 0,
})
t2 = dec.get("timings", {})
u2 = dec.get("usage", {})

out = {
    "label": label,
    "prefill_tok": u1.get("prompt_tokens"),
    "prefill_ms": round(t1.get("prompt_ms", 0), 1),
    "prefill_tps": round(t1.get("prompt_per_second", 0), 1),
    "decode_tok": u2.get("completion_tokens"),
    "decode_ms": round(t2.get("predicted_ms", 0), 1),
    "decode_tps": round(t2.get("predicted_per_second", 0), 2),
    "ttft_ms": round(t2.get("prompt_ms", 0), 1),
}
print(json.dumps(out, ensure_ascii=False))
with open(r"C:/AI/logs/bench-results.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps(out, ensure_ascii=False) + "\n")
