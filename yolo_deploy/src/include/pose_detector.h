// pose_detector.h — YOLOv8-pose (NCNN, CPU-only) decode + posture assessment
// RG660MK-EU (MediaTek T930, Cortex-A55 x4, aarch64, OpenWrt/musl)
//
// 本头文件把「模型推理 / 坐姿几何 / 人脸区域估算 / 时间平滑」封装为可复用类，
// 供静态图片验证入口 (app_image) 与摄像头入口 (app_camera) 共用，保证两条链路
// 使用完全一致的预处理、解码、NMS 与坐姿判定逻辑。
//
// 关键点约定: COCO-17。仅使用 pose 模型，不加载第二个 yolov8n 检测模型。
#pragma once

#include <string>
#include <vector>
#include <cmath>
#include <cstdint>

#include "net.h"  // ncnn

namespace rgpose {

// ---- COCO-17 关键点索引 ----
enum {
    KP_NOSE = 0, KP_L_EYE = 1, KP_R_EYE = 2, KP_L_EAR = 3, KP_R_EAR = 4,
    KP_L_SHO = 5, KP_R_SHO = 6, KP_L_ELB = 7, KP_R_ELB = 8,
    KP_L_WRI = 9, KP_R_WRI = 10, KP_L_HIP = 11, KP_R_HIP = 12,
    KP_L_KNE = 13, KP_R_KNE = 14, KP_L_ANK = 15, KP_R_ANK = 16,
    NUM_KPT = 17
};

struct Keypoint {
    float x = 0.f;   // 原图像素坐标
    float y = 0.f;
    float conf = 0.f;
};

struct Rect {
    float x = 0.f, y = 0.f, w = 0.f, h = 0.f;  // 左上角 + 宽高，原图像素
    float area() const { return w * h; }
};

struct Person {
    Rect box;                        // 人员框
    float score = 0.f;               // 人员框置信度
    Keypoint kpt[NUM_KPT];           // 17 个关键点
    bool has_face = false;           // 是否输出人脸区域估算
    Rect face_region;                // 人脸区域估算(仅几何估算,非人脸识别)
    std::string posture_label;       // 坐姿标签
    std::vector<std::string> posture_issues;
    // 关键几何指标(缺失则为 NAN)
    float shoulder_tilt = NAN;       // 肩倾角(度)
    float head_forward   = NAN;      // 头水平偏移比
    float head_drop      = NAN;      // 头肩垂距比
    float trunk_tilt     = NAN;      // 躯干侧倾角(度)
};

// ---- 可配置参数(全部可由 CLI / 配置文件覆盖)----
struct Config {
    int   target_size   = 320;   // 网络输入边长(等比 letterbox)
    int   num_threads   = 2;     // 1..4;T930 为 4x 同构 A55,仅调线程数
    float conf_thresh   = 0.35f; // 人员框置信度阈值(0.35 抑制空场景假阳,真人~0.91 不受影响)
    float nms_thresh    = 0.45f; // NMS IoU 阈值
    float kpt_thresh    = 0.40f; // 关键点置信度阈值(与原 Python 一致)

    // 坐姿几何阈值(移植自 test_face_posture.py,可配置)
    float shoulder_tilt_thresh = 10.0f;  // >10° 判高低肩
    float head_drop_thresh     = -0.15f; // head_drop > -0.15 判低头/含胸
    float head_forward_thresh  = 0.35f;  // |head_forward| > 0.35 判头偏斜
    float trunk_tilt_thresh    = 12.0f;  // >12° 判身体歪斜

    // 人脸区域估算(移植自 face_box_from_kpts,可配置)
    float face_expand   = 1.6f;  // 外扩系数
    float face_pad      = 10.0f; // 外扩常数像素
    int   face_min_pts  = 2;     // 有效头部关键点下限,不足则不输出人脸框

    // 时间平滑 / 迟滞 / 告警(仅摄像头流用;静态图片不启用)
    int   smooth_window   = 5;    // 最近 N 帧多数表决
    float alert_hold_sec  = 2.0f; // 异常持续 >=2s 才告警
    float hysteresis      = 0.15f;// 阈值附近迟滞带(相对比例)
};

// 单帧推理耗时(毫秒)
struct Timing {
    double pre_ms = 0, infer_ms = 0, post_ms = 0, total_ms = 0;
};

class PoseDetector {
public:
    PoseDetector() = default;
    ~PoseDetector();

    // 加载一次模型(param + bin)。返回 0 成功。
    int load(const std::string& param_path, const std::string& bin_path, const Config& cfg);

    // 对一张 RGB 图 (packed, w*h*3, 行优先) 做推理，输出 persons。
    int infer_rgb(const unsigned char* rgb, int w, int h,
                  std::vector<Person>& persons, Timing& t);

    const Config& config() const { return cfg_; }
    void set_config(const Config& c) { cfg_ = c; }

private:
    ncnn::Net net_;
    Config cfg_;
    bool loaded_ = false;

    void assess_posture(Person& p) const;
    void estimate_face_region(Person& p, int img_w, int img_h) const;
};

// ---- 静态工具函数(供 NMS / IoU 复用)----
float iou(const Rect& a, const Rect& b);
void  nms_sorted(std::vector<Person>& cands, float nms_thresh);

}  // namespace rgpose
