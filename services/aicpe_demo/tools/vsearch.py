#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vsearch — 自然语言视频找帧（基于 vindex 索引；生成者：hermes 2026-11-06）

用法：
  vsearch.py --index /data/vindex/concert --query "找出画面里出现广州塔的帧" [--json] [--out /path.jpg]
  vsearch.py --video /data/demo.mp4 --query "广州塔"      # 自动找同名索引目录
"""
import argparse
import json
import os
import re
import shutil

SYN = {
    "小蛮腰": "广州塔", "塔": "广州塔", "地标": "广州塔", "电视塔": "广州塔",
    "汽车": "汽车", "车": "汽车", "舞台": "舞台", "屏幕": "屏幕", "大屏": "屏幕",
    "观众": "观众", "人": "人物", "乐队": "演出", "演唱会": "演出",
}


def tokens(q):
    q = q.lower()
    out = set()
    for zh, std in SYN.items():
        if zh in q:
            out.add(std)
    for w in re.findall(r"[a-z0-9]{2,}", q):
        out.add(w)
    # 中文二元组，覆盖未登录词
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        for i in range(len(seg) - 1):
            out.add(seg[i:i + 2])
    return out


def score(item, toks):
    text = (item.get("caption", "") + " " + " ".join(item.get("tags", []))).lower()
    s = 0
    for t in toks:
        if t in text:
            s += 3 if len(t) >= 2 else 1
    if s > 0:
        s += int(item.get("weight", 0))
    return s


def search(index_dir, query, topk=5):
    idx = json.load(open(os.path.join(index_dir, "index.json"), encoding="utf-8"))
    toks = tokens(query)
    scored = []
    for it in idx["frames"]:
        scored.append((score(it, toks), it))
    scored.sort(key=lambda x: (-x[0], x[1]["t"]))
    best_score, best = scored[0]
    cands = [{"t": it["t"], "score": sc, "caption": it["caption"], "frame": it["frame"]}
             for sc, it in scored[:topk] if sc > 0]
    return {"video": idx["video"], "query": query, "matched": best_score > 0,
            "best": {"t": best["t"], "score": best_score, "caption": best["caption"], "frame": best["frame"]},
            "candidates": cands}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index")
    ap.add_argument("--video")
    ap.add_argument("--query", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    index_dir = a.index
    if not index_dir and a.video:
        index_dir = "/data/vindex/" + os.path.splitext(os.path.basename(a.video))[0]
    if not index_dir or not os.path.exists(os.path.join(index_dir, "index.json")):
        print(json.dumps({"error": "index not found", "index": index_dir}, ensure_ascii=False))
        return 1
    r = search(index_dir, a.query)
    if a.out and r["best"].get("frame") and os.path.exists(r["best"]["frame"]):
        shutil.copyfile(r["best"]["frame"], a.out)
        r["out"] = a.out
    print(json.dumps(r, ensure_ascii=False) if a.json else
          "命中：%s 秒 ｜ %s ｜ score=%s ｜ %s" % (r["best"]["t"], r["best"]["caption"], r["best"]["score"], r["best"]["frame"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
