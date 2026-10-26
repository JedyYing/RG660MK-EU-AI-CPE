#!/usr/bin/env python3
"""Parallel byte-range resume for a segmented download, then join in order.

Expects parts from dl-parallel.sh (part-NN / part-NN.tmp); only fetches the
missing tail of each part, verifies sizes, then concatenates.
Env: DL_WORKERS (default 12)
"""
import os
import sys
import time
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = sys.argv[1]
TOTAL = int(sys.argv[2])
OUT = sys.argv[3]
PARTS = int(sys.argv[4]) if len(sys.argv) > 4 else 32
LOG = sys.argv[5] if len(sys.argv) > 5 else r"C:\AI\logs\resume.log"
WORKERS = int(os.environ.get("DL_WORKERS", "12"))

CHUNK = (TOTAL + PARTS - 1) // PARTS
ctx = ssl.create_default_context()
logf = open(LOG, "a", encoding="utf-8")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    logf.write(line + "\n")
    logf.flush()


def fetch_range(start, end, path, mode):
    for attempt in range(1, 8):
        try:
            req = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}",
                                                       "User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=180, context=ctx) as r, open(path, mode) as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            return True
        except Exception as e:
            log(f"  retry {attempt} [{start}-{end}]: {type(e).__name__}")
            time.sleep(3 * attempt)
            mode = "ab"
    return False


def part_path(i):
    return os.path.join(OUT + ".parts", f"part-{i:02d}")


def one(i):
    start = i * CHUNK
    end = min(start + CHUNK - 1, TOTAL - 1)
    want = end - start + 1
    part, tmp = part_path(i), part_path(i) + ".tmp"
    if os.path.exists(part) and os.path.getsize(part) == want:
        return "skip"
    have = 0
    if os.path.exists(tmp):
        have = os.path.getsize(tmp)
    elif os.path.exists(part):
        os.rename(part, tmp)
        have = os.path.getsize(tmp)
    if have > want:
        os.unlink(tmp)
        have = 0
    if have < want:
        log(f"part {i:02d}: have {have/2**20:.0f}/{want/2**20:.0f} MB, fetching tail")
        if not fetch_range(start + have, end, tmp, "ab"):
            log(f"part {i:02d}: FAILED")
            return "fail"
    if os.path.getsize(tmp) == want:
        os.replace(tmp, part)
        log(f"part {i:02d}: complete")
        return "ok"
    log(f"part {i:02d}: size {os.path.getsize(tmp)} != {want}")
    return "short"


def main():
    log(f"resume start: parts={PARTS} workers={WORKERS} total={TOTAL}")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(one, range(PARTS)))
    log("part results: " + str({k: results.count(k) for k in set(results)}))

    missing = [i for i in range(PARTS)
               if not (os.path.exists(part_path(i))
                       and os.path.getsize(part_path(i)) == min((i + 1) * CHUNK, TOTAL) - i * CHUNK)]
    if missing:
        log(f"INCOMPLETE parts: {missing}")
        return 1

    log("all parts complete -> concatenating")
    with open(OUT, "wb") as out:
        for i in range(PARTS):
            with open(part_path(i), "rb") as fh:
                while True:
                    b = fh.read(8 << 20)
                    if not b:
                        break
                    out.write(b)
    size = os.path.getsize(OUT)
    ok = size == TOTAL
    log(f"joined size={size} expected={TOTAL} {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
