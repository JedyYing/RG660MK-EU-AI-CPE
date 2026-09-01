#!/usr/bin/env python3
# compare_baseline.py — 阶段A一致性判定:设备 JSONL vs PC 基线 JSONL
# 判据:人数一致;人员框 IoU>=0.90;关键点平均误差<=1%图像宽高;坐姿标签一致。
import json, sys, math

def load(p):
    d={}
    for l in open(p):
        l=l.strip()
        if not l: continue
        o=json.loads(l)
        d[o["image"].split("/")[-1]]=o
    return d

def iou(a,b):
    ax,ay,aw,ah=a; bx,by,bw,bh=b
    x1=max(ax,bx); y1=max(ay,by); x2=min(ax+aw,bx+bw); y2=min(ay+ah,by+bh)
    iw=max(0,x2-x1); ih=max(0,y2-y1); inter=iw*ih
    uni=aw*ah+bw*bh-inter
    return inter/uni if uni>0 else 0

def match(ba,de):
    # 按框中心贪心匹配
    used=set(); pairs=[]
    for i,pa in enumerate(ba):
        best=-1; bj=-1
        for j,pb in enumerate(de):
            if j in used: continue
            v=iou(pa["box"],pb["box"])
            if v>best: best=v; bj=j
        if bj>=0: used.add(bj); pairs.append((pa,de[bj],best))
    return pairs

def main():
    base=load(sys.argv[1]); dev=load(sys.argv[2])
    allpass=True; rows=[]
    for name in sorted(base):
        b=base[name]; d=dev.get(name)
        if d is None:
            rows.append((name,"MISSING","设备无此图结果")); allpass=False; continue
        npb=b["num_person"]; npd=d["num_person"]
        W=b["width"]; H=b["height"]; tol=0.01*max(W,H)
        ok_n = (npb==npd)
        pairs=match(b.get("persons",[]),d.get("persons",[]))
        min_iou=1.0; max_kerr=0.0; label_ok=True
        for pa,pb,v in pairs:
            min_iou=min(min_iou,v)
            # 关键点平均误差(仅比较双方 conf>0.4 的点)
            errs=[]
            for ka,kb in zip(pa["keypoints"],pb["keypoints"]):
                if ka[2]>0.4 and kb[2]>0.4:
                    errs.append(math.hypot(ka[0]-kb[0],ka[1]-kb[1]))
            if errs: max_kerr=max(max_kerr,sum(errs)/len(errs))
            if pa["posture"]!=pb["posture"]: label_ok=False
        ok_iou = (not pairs) or (min_iou>=0.90)
        ok_k   = (max_kerr<=tol)
        passed = ok_n and ok_iou and ok_k and label_ok
        allpass = allpass and passed
        rows.append((name,"PASS" if passed else "FAIL",
          f"人数{npb}/{npd} minIoU={min_iou:.3f} kptErr={max_kerr:.1f}px(<= {tol:.1f}) 标签{'一致' if label_ok else '不一致'}"))
    print("===== 阶段A 设备 vs PC 基线 一致性 =====")
    for n,s,det in rows: print(f"[{s}] {n}: {det}")
    print("总判定:", "PASS ✅" if allpass else "FAIL ❌")
    sys.exit(0 if allpass else 1)

if __name__=="__main__": main()
