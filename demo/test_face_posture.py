#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO 人脸识别 + 坐姿检测 联合测试
- 人脸: 用 person 检测框 + pose 头部关键点(鼻/眼/耳)定位人脸区域
- 坐姿: 用 COCO 17 关键点计算 头前倾角、肩部倾斜、身体侧倾, 判定坐姿好坏
运行环境: 本机 ultralytics + 内置摄像头(video0)
"""
import cv2, math, time, sys
from ultralytics import YOLO

DET_MODEL  = "/home/jedyying/Downloads/yolov8n.pt"
POSE_MODEL = "/home/jedyying/sg560d_pose_qnn/models/yolo11n-pose.pt"
OUT_DIR    = "/home/jedyying/Agent工作区/yolo_demo/输出"
CAM_INDEX  = 0
N_FRAMES   = 40          # 采集帧数
SAVE_EVERY = 10          # 每隔多少帧存一张标注图

# COCO 关键点索引
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHO, R_SHO, L_HIP, R_HIP = 5, 6, 11, 12

def angle_from_vertical(dx, dy):
    """向量与竖直方向夹角(度)"""
    return abs(math.degrees(math.atan2(dx, dy)))

def assess_posture(kpts, conf):
    """输入单人 17x2 关键点与置信度, 返回(坐姿标签, 指标dict)"""
    def ok(i): return conf[i] > 0.4
    metrics = {}
    issues = []

    # 1) 肩部水平倾斜: 双肩连线与水平线夹角
    if ok(L_SHO) and ok(R_SHO):
        dx = kpts[R_SHO][0]-kpts[L_SHO][0]
        dy = kpts[R_SHO][1]-kpts[L_SHO][1]
        shoulder_tilt = abs(math.degrees(math.atan2(dy, dx)))
        shoulder_tilt = min(shoulder_tilt, 180-shoulder_tilt)
        metrics["肩倾角"] = round(shoulder_tilt,1)
        if shoulder_tilt > 10: issues.append("高低肩")

    # 2) 头前倾: 鼻子相对双肩中点的水平偏移 / 肩宽
    if ok(NOSE) and ok(L_SHO) and ok(R_SHO):
        sho_mid_x = (kpts[L_SHO][0]+kpts[R_SHO][0])/2
        sho_mid_y = (kpts[L_SHO][1]+kpts[R_SHO][1])/2
        sho_w = max(1.0, math.hypot(kpts[R_SHO][0]-kpts[L_SHO][0], kpts[R_SHO][1]-kpts[L_SHO][1]))
        fwd = (kpts[NOSE][0]-sho_mid_x)/sho_w        # 水平前伸比例
        drop = (kpts[NOSE][1]-sho_mid_y)/sho_w       # 头相对肩下沉比例(越大越低头)
        metrics["头水平偏移比"] = round(fwd,2)
        metrics["头肩垂距比"] = round(drop,2)
        # 头离肩太近(缩脖含胸低头) 或 明显偏一侧
        if drop > -0.15: issues.append("低头/含胸")
        if abs(fwd) > 0.35: issues.append("头偏斜")

    # 3) 躯干侧倾: 肩中点->髋中点连线与竖直夹角
    if ok(L_SHO) and ok(R_SHO) and ok(L_HIP) and ok(R_HIP):
        sx=(kpts[L_SHO][0]+kpts[R_SHO][0])/2; sy=(kpts[L_SHO][1]+kpts[R_SHO][1])/2
        hx=(kpts[L_HIP][0]+kpts[R_HIP][0])/2; hy=(kpts[L_HIP][1]+kpts[R_HIP][1])/2
        trunk = angle_from_vertical(hx-sx, hy-sy)
        metrics["躯干侧倾角"] = round(trunk,1)
        if trunk > 12: issues.append("身体歪斜")

    label = "坐姿良好" if not issues else "坐姿不良: " + "、".join(issues)
    return label, metrics

def face_box_from_kpts(kpts, conf, frame_shape):
    """用头部关键点估计人脸框"""
    ids=[NOSE,L_EYE,R_EYE,L_EAR,R_EAR]
    pts=[kpts[i] for i in ids if conf[i]>0.4]
    if len(pts)<2: return None
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    w=max(xs)-min(xs); h=max(ys)-min(ys)
    cx=(max(xs)+min(xs))/2; cy=(max(ys)+min(ys))/2
    side=max(w,h)*1.6+10
    H,W=frame_shape[:2]
    x1=int(max(0,cx-side/2)); y1=int(max(0,cy-side*0.6))
    x2=int(min(W,cx+side/2)); y2=int(min(H,cy+side*0.6))
    return (x1,y1,x2,y2)

def main():
    print("加载模型...")
    det=YOLO(DET_MODEL); pose=YOLO(POSE_MODEL)
    cap=cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        print("ERROR 摄像头打不开"); sys.exit(1)
    time.sleep(1.0)  # 预热

    face_hits=0; pose_hits=0; total=0
    last_summary={}
    for f in range(N_FRAMES):
        ok,frame=cap.read()
        if not ok: continue
        total+=1
        dets=det(frame, classes=[0], verbose=False)[0]   # 只要 person
        poses=pose(frame, verbose=False)[0]
        n_person=len(dets.boxes) if dets.boxes is not None else 0

        vis=frame.copy()
        # 画 person 检测框
        if dets.boxes is not None:
            for b in dets.boxes:
                x1,y1,x2,y2=map(int,b.xyxy[0]); c=float(b.conf[0])
                cv2.rectangle(vis,(x1,y1),(x2,y2),(0,200,0),2)
                cv2.putText(vis,f"person {c:.2f}",(x1,y1-6),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,200,0),1)

        # 姿态 + 人脸
        summary_lines=[]
        if poses.keypoints is not None and len(poses.keypoints)>0:
            kp_xy=poses.keypoints.xy.cpu().numpy()
            kp_cf=poses.keypoints.conf.cpu().numpy() if poses.keypoints.conf is not None else None
            for i in range(len(kp_xy)):
                kpts=kp_xy[i]; conf=kp_cf[i] if kp_cf is not None else [1.0]*17
                if len(kpts)<17 or len(conf)<17: continue   # 空/不完整关键点跳过
                # 人脸
                fb=face_box_from_kpts(kpts,conf,frame.shape)
                if fb:
                    face_hits+=1
                    cv2.rectangle(vis,(fb[0],fb[1]),(fb[2],fb[3]),(255,120,0),2)
                    cv2.putText(vis,"face",(fb[0],fb[1]-6),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,120,0),1)
                # 坐姿
                label,metrics=assess_posture(kpts,conf)
                pose_hits+=1
                color=(0,180,0) if label=="坐姿良好" else (0,0,230)
                cv2.putText(vis,label,(10,30+i*24),cv2.FONT_HERSHEY_SIMPLEX,0.6,color,2)
                summary_lines.append((label,metrics))
                # 画关键点
                for j in range(17):
                    if conf[j]>0.4:
                        cv2.circle(vis,(int(kpts[j][0]),int(kpts[j][1])),3,(0,255,255),-1)

        if summary_lines: last_summary=summary_lines[0]
        if f % SAVE_EVERY == 0:
            out=f"{OUT_DIR}/frame_{f:03d}.jpg"; cv2.imwrite(out,vis)

    cap.release()
    # 结果汇总
    print("="*50)
    print(f"采集帧数: {total}")
    print(f"检测到人脸的帧数: {face_hits}")
    print(f"完成坐姿评估的帧数: {pose_hits}")
    if last_summary:
        lbl,met=last_summary
        print(f"最近一帧坐姿判定: {lbl}")
        print(f"坐姿指标: {met}")
    print(f"标注图已保存至: {OUT_DIR}/frame_*.jpg")
    print("="*50)

if __name__=="__main__":
    main()
