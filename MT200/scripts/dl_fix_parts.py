#!/usr/bin/env python3
"""Re-fetch specific oversized/short parts, verifying Content-Range, in 64 MB sub-chunks."""
import os
import sys
import time
import ssl
import urllib.request

URL = sys.argv[1]
TOTAL = int(sys.argv[2])
OUT = sys.argv[3]
PARTS = int(sys.argv[4])
TARGETS = [int(x) for x in sys.argv[5].split(",")]
LOG = sys.argv[6] if len(sys.argv) > 6 else r"D:\AI\logs\fixparts.log"

CHUNK = (TOTAL + PARTS - 1) // PARTS
SUB = 64 * 1024 * 1024
ctx = ssl.create_default_context()
logf = open(LOG, "a", encoding="utf-8")


def log(m):
    s = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(s, flush=True)
    logf.write(s + "\n")
    logf.flush()


def fetch(i):
    start = i * CHUNK
    end = min(start + CHUNK - 1, TOTAL - 1)
    want = end - start + 1
    part = os.path.join(OUT + ".parts", f"part-{i:02d}")
    tmp = part + ".tmp"
    for f in (part, tmp):
        if os.path.exists(f):
            os.unlink(f)
    pos = start
    while pos <= end:
        sub_end = min(pos + SUB - 1, end)
        ok = False
        for attempt in range(1, 6):
            try:
                req = urllib.request.Request(
                    URL,
                    headers={"Range": f"bytes={pos}-{sub_end}",
                             "User-Agent": "curl/8",
                             "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
                    cr = r.headers.get("Content-Range", "")
                    if not cr.startswith(f"bytes {pos}-"):
                        log(f"  part {i:02d}: bad Content-Range '{cr}' at {pos}, retry")
                        raise ValueError("bad range")
                    data = r.read()
                expect = sub_end - pos + 1
                if len(data) != expect:
                    log(f"  part {i:02d}: got {len(data)} != {expect} at {pos}, retry")
                    raise ValueError("short")
                with open(tmp, "ab") as fh:
                    fh.write(data)
                ok = True
                break
            except Exception as e:
                log(f"  part {i:02d} [{pos}-{sub_end}] attempt {attempt}: {type(e).__name__} {e}")
                time.sleep(2 * attempt)
        if not ok:
            log(f"part {i:02d}: FAILED at {pos}")
            return False
        pos = sub_end + 1
    size = os.path.getsize(tmp)
    if size == want:
        os.replace(tmp, part)
        log(f"part {i:02d}: complete {size} bytes")
        return True
    log(f"part {i:02d}: size {size} != {want}")
    return False


ok = all(fetch(i) for i in TARGETS)
log("fix result: " + ("ALL OK" if ok else "FAILURES REMAIN"))
sys.exit(0 if ok else 1)
