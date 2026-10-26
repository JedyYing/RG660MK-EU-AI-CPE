#!/usr/bin/env python3
"""Finish specific parts: resume from existing .tmp (or restart if oversized),
verifying Content-Range on every 64 MB sub-request.
usage: dl_finish.py URL TOTAL OUT PARTS 1,18,31
"""
import os
import sys
import time
import ssl
import urllib.request

URL, TOTAL, OUT, PARTS = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
TARGETS = [int(x) for x in sys.argv[5].split(",")]
CHUNK = (TOTAL + PARTS - 1) // PARTS
SUB = 64 * 1024 * 1024
ctx = ssl.create_default_context()


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def fetch(i):
    start = i * CHUNK
    end = min(start + CHUNK - 1, TOTAL - 1)
    want = end - start + 1
    part = os.path.join(OUT + ".parts", f"part-{i:02d}")
    tmp = part + ".tmp"
    if os.path.exists(part) and os.path.getsize(part) == want:
        log(f"part {i:02d}: already complete")
        return True
    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if have > want:
        log(f"part {i:02d}: tmp {have} > want {want}, restarting")
        os.unlink(tmp)
        have = 0
    pos = start + have
    log(f"part {i:02d}: resuming at {have/2**20:.0f}/{want/2**20:.0f} MB")
    while pos <= end:
        sub_end = min(pos + SUB - 1, end)
        ok = False
        for attempt in range(1, 6):
            try:
                req = urllib.request.Request(URL, headers={
                    "Range": f"bytes={pos}-{sub_end}", "User-Agent": "curl/8",
                    "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
                    cr = r.headers.get("Content-Range", "")
                    if not cr.startswith(f"bytes {pos}-"):
                        raise ValueError(f"bad Content-Range: {cr!r}")
                    data = r.read()
                expect = sub_end - pos + 1
                if len(data) != expect:
                    raise ValueError(f"short: {len(data)} != {expect}")
                with open(tmp, "ab") as fh:
                    fh.write(data)
                ok = True
                break
            except Exception as e:
                log(f"  part {i:02d} @{pos}: attempt {attempt} {type(e).__name__} {e}")
                time.sleep(2 * attempt)
        if not ok:
            log(f"part {i:02d}: FAILED at {pos}")
            return False
        pos = sub_end + 1
        log(f"part {i:02d}: {min(pos-start, want)/2**20:.0f}/{want/2**20:.0f} MB")
    size = os.path.getsize(tmp)
    if size == want:
        os.replace(tmp, part)
        log(f"part {i:02d}: complete")
        return True
    log(f"part {i:02d}: final size {size} != {want}")
    return False


ok = all(fetch(i) for i in TARGETS)
log("FINISH " + ("OK" if ok else "INCOMPLETE"))
sys.exit(0 if ok else 1)
