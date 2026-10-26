#!/usr/bin/env python3
"""Download listed parts from scratch (single process, mutually exclusive).

Verifies HTTP 206 + matching Content-Range for every 64 MB sub-request, so a
server that ignores Range cannot corrupt the file.
usage: dl_parts.py URL TOTAL OUT PARTS 1,2,18,31 LOGFILE
"""
import os
import sys
import time
import ssl
import urllib.request

URL, TOTAL, OUT, PARTS = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
TARGETS = [int(x) for x in sys.argv[5].split(",")]
LOG = sys.argv[6]
CHUNK = (TOTAL + PARTS - 1) // PARTS
SUB = 64 * 1024 * 1024
ctx = ssl.create_default_context()
lf = open(LOG, "a", encoding="utf-8")


def log(m):
    s = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(s, flush=True)
    lf.write(s + "\n")
    lf.flush()


def fetch(i):
    start, end = i * CHUNK, min((i + 1) * CHUNK - 1, TOTAL - 1)
    want = end - start + 1
    part = os.path.join(OUT + ".parts", f"part-{i:02d}")
    tmp = part + ".tmp"
    for f in (part, tmp):
        if os.path.exists(f):
            os.unlink(f)
    pos, t0 = start, time.time()
    while pos <= end:
        sub_end = min(pos + SUB - 1, end)
        for attempt in range(1, 7):
            try:
                req = urllib.request.Request(URL, headers={
                    "Range": f"bytes={pos}-{sub_end}", "User-Agent": "curl/8",
                    "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
                    if r.status != 206:
                        raise ValueError(f"status {r.status}, not 206")
                    cr = r.headers.get("Content-Range", "")
                    if not cr.startswith(f"bytes {pos}-{sub_end}/"):
                        raise ValueError(f"bad Content-Range {cr!r}")
                    data = r.read()
                if len(data) != sub_end - pos + 1:
                    raise ValueError(f"short {len(data)}")
                with open(tmp, "ab") as fh:
                    fh.write(data)
                break
            except Exception as e:
                if attempt == 6:
                    log(f"part {i:02d}: FAILED at {pos} ({e})")
                    return False
                time.sleep(2 * attempt)
        pos = sub_end + 1
    got = os.path.getsize(tmp)
    if got != want:
        log(f"part {i:02d}: size {got} != {want}")
        return False
    os.replace(tmp, part)
    log(f"part {i:02d}: complete {want} bytes in {time.time()-t0:.0f}s "
        f"({want/2**20/(time.time()-t0):.2f} MB/s)")
    return True


ok = True
for i in TARGETS:
    log(f"--- part {i:02d} ---")
    if not fetch(i):
        ok = False
log("ALL TARGETS OK" if ok else "SOME TARGETS FAILED")
sys.exit(0 if ok else 1)
