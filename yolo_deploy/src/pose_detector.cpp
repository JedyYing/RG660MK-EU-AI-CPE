// pose_detector.cpp — YOLOv8-pose NCNN decode + posture geometry
// 参考 ncnn 官方 examples/yolov8-pose 的解码结构，坐姿/人脸几何移植自
// test_face_posture.py 并保持阈值一致。CPU-only, use_vulkan_compute=false。
#include "pose_detector.h"

#include <algorithm>
#include <cstring>
#include <chrono>

namespace rgpose {

// 单调时钟毫秒(替代 ncnn::get_current_time,后者依赖 benchmark 模块)
static inline double now_ms() {
    using namespace std::chrono;
    return duration_cast<duration<double, std::milli>>(
               steady_clock::now().time_since_epoch()).count();
}

PoseDetector::~PoseDetector() {
    net_.clear();
}

int PoseDetector::load(const std::string& param_path, const std::string& bin_path,
                       const Config& cfg) {
    cfg_ = cfg;

    // 纯 CPU:关闭 Vulkan,设置线程数与轻量选项
    net_.opt = ncnn::Option();
    net_.opt.use_vulkan_compute = false;      // 硬性要求:关闭 Vulkan
    net_.opt.num_threads = cfg_.num_threads;  // 1..4
    net_.opt.use_packing_layout = true;
    net_.opt.use_fp16_packed = false;         // FP32 部署,先保证正确性
    net_.opt.use_fp16_storage = false;
    net_.opt.use_fp16_arithmetic = false;
    net_.opt.lightmode = true;                // 释放中间 blob,降内存

    if (net_.load_param(param_path.c_str()) != 0) return -1;
    if (net_.load_model(bin_path.c_str()) != 0)  return -2;
    loaded_ = true;
    return 0;
}

float iou(const Rect& a, const Rect& b) {
    float x1 = std::max(a.x, b.x);
    float y1 = std::max(a.y, b.y);
    float x2 = std::min(a.x + a.w, b.x + b.w);
    float y2 = std::min(a.y + a.h, b.y + b.h);
    float iw = std::max(0.f, x2 - x1);
    float ih = std::max(0.f, y2 - y1);
    float inter = iw * ih;
    float uni = a.area() + b.area() - inter;
    return uni > 0.f ? inter / uni : 0.f;
}

void nms_sorted(std::vector<Person>& cands, float nms_thresh) {
    std::sort(cands.begin(), cands.end(),
              [](const Person& p, const Person& q) { return p.score > q.score; });
    std::vector<Person> keep;
    keep.reserve(cands.size());
    std::vector<char> removed(cands.size(), 0);
    for (size_t i = 0; i < cands.size(); ++i) {
        if (removed[i]) continue;
        keep.push_back(cands[i]);
        for (size_t j = i + 1; j < cands.size(); ++j) {
            if (removed[j]) continue;
            if (iou(cands[i].box, cands[j].box) > nms_thresh) removed[j] = 1;
        }
    }
    cands.swap(keep);
}

static inline float sigmoid(float x) { return 1.f / (1.f + std::exp(-x)); }

// ---- 坐姿几何(移植 test_face_posture.py: assess_posture)----
void PoseDetector::assess_posture(Person& p) const {
    auto ok = [&](int i) { return p.kpt[i].conf > cfg_.kpt_thresh; };
    p.posture_issues.clear();

    // 1) 肩部水平倾斜
    if (ok(KP_L_SHO) && ok(KP_R_SHO)) {
        float dx = p.kpt[KP_R_SHO].x - p.kpt[KP_L_SHO].x;
        float dy = p.kpt[KP_R_SHO].y - p.kpt[KP_L_SHO].y;
        float tilt = std::fabs(std::atan2(dy, dx) * 180.f / M_PI);
        tilt = std::min(tilt, 180.f - tilt);
        p.shoulder_tilt = tilt;
        if (tilt > cfg_.shoulder_tilt_thresh) p.posture_issues.push_back("高低肩");
    }

    // 2) 头前倾: 鼻相对双肩中点
    if (ok(KP_NOSE) && ok(KP_L_SHO) && ok(KP_R_SHO)) {
        float smx = (p.kpt[KP_L_SHO].x + p.kpt[KP_R_SHO].x) / 2.f;
        float smy = (p.kpt[KP_L_SHO].y + p.kpt[KP_R_SHO].y) / 2.f;
        float sw = std::max(1.f, std::hypot(p.kpt[KP_R_SHO].x - p.kpt[KP_L_SHO].x,
                                            p.kpt[KP_R_SHO].y - p.kpt[KP_L_SHO].y));
        float fwd  = (p.kpt[KP_NOSE].x - smx) / sw;
        float drop = (p.kpt[KP_NOSE].y - smy) / sw;
        p.head_forward = fwd;
        p.head_drop = drop;
        if (drop > cfg_.head_drop_thresh) p.posture_issues.push_back("低头/含胸");
        if (std::fabs(fwd) > cfg_.head_forward_thresh) p.posture_issues.push_back("头偏斜");
    }

    // 3) 躯干侧倾: 肩中点->髋中点 与竖直夹角
    if (ok(KP_L_SHO) && ok(KP_R_SHO) && ok(KP_L_HIP) && ok(KP_R_HIP)) {
        float sx = (p.kpt[KP_L_SHO].x + p.kpt[KP_R_SHO].x) / 2.f;
        float sy = (p.kpt[KP_L_SHO].y + p.kpt[KP_R_SHO].y) / 2.f;
        float hx = (p.kpt[KP_L_HIP].x + p.kpt[KP_R_HIP].x) / 2.f;
        float hy = (p.kpt[KP_L_HIP].y + p.kpt[KP_R_HIP].y) / 2.f;
        float trunk = std::fabs(std::atan2(hx - sx, hy - sy) * 180.f / M_PI);
        p.trunk_tilt = trunk;
        if (trunk > cfg_.trunk_tilt_thresh) p.posture_issues.push_back("身体歪斜");
    }

    if (p.posture_issues.empty()) {
        p.posture_label = "坐姿良好";
    } else {
        p.posture_label = "坐姿不良: ";
        for (size_t i = 0; i < p.posture_issues.size(); ++i) {
            if (i) p.posture_label += "、";
            p.posture_label += p.posture_issues[i];
        }
    }
}

// ---- 人脸区域估算(移植 face_box_from_kpts)----
void PoseDetector::estimate_face_region(Person& p, int img_w, int img_h) const {
    const int ids[5] = {KP_NOSE, KP_L_EYE, KP_R_EYE, KP_L_EAR, KP_R_EAR};
    std::vector<float> xs, ys;
    for (int k = 0; k < 5; ++k) {
        int i = ids[k];
        if (p.kpt[i].conf > cfg_.kpt_thresh) { xs.push_back(p.kpt[i].x); ys.push_back(p.kpt[i].y); }
    }
    if ((int)xs.size() < cfg_.face_min_pts) { p.has_face = false; return; }
    float minx = *std::min_element(xs.begin(), xs.end());
    float maxx = *std::max_element(xs.begin(), xs.end());
    float miny = *std::min_element(ys.begin(), ys.end());
    float maxy = *std::max_element(ys.begin(), ys.end());
    float w = maxx - minx, h = maxy - miny;
    float cx = (maxx + minx) / 2.f, cy = (maxy + miny) / 2.f;
    float side = std::max(w, h) * cfg_.face_expand + cfg_.face_pad;
    float x1 = std::max(0.f, cx - side / 2.f);
    float y1 = std::max(0.f, cy - side * 0.6f);
    float x2 = std::min((float)img_w, cx + side / 2.f);
    float y2 = std::min((float)img_h, cy + side * 0.6f);
    p.has_face = true;
    p.face_region = Rect{x1, y1, x2 - x1, y2 - y1};  // 已裁剪到图像边界
}

// ---- 主推理 ----
int PoseDetector::infer_rgb(const unsigned char* rgb, int w, int h,
                            std::vector<Person>& persons, Timing& t) {
    persons.clear();
    if (!loaded_) return -1;

    double t0 = now_ms();

    // 等比 letterbox 到 target x target
    const int target = cfg_.target_size;
    float scale = std::min((float)target / w, (float)target / h);
    int nw = (int)std::round(w * scale);
    int nh = (int)std::round(h * scale);

    ncnn::Mat in = ncnn::Mat::from_pixels_resize(
        rgb, ncnn::Mat::PIXEL_RGB, w, h, nw, nh);

    // padding 到 target(YOLOv8 用 114 灰边)
    int wpad = target - nw;
    int hpad = target - nh;
    int top = hpad / 2, bottom = hpad - top;
    int left = wpad / 2, right = wpad - left;
    ncnn::Mat in_pad;
    ncnn::copy_make_border(in, in_pad, top, bottom, left, right,
                           ncnn::BORDER_CONSTANT, 114.f);

    // 归一化 1/255,RGB 顺序(导出即 RGB)
    const float norm[3] = {1 / 255.f, 1 / 255.f, 1 / 255.f};
    in_pad.substract_mean_normalize(nullptr, norm);

    double t1 = now_ms();

    ncnn::Extractor ex = net_.create_extractor();
    ex.input("in0", in_pad);
    ncnn::Mat out;
    ex.extract("out0", out);   // 期望 [56, 8400]: 4 box + 1 score + 51 kpt

    double t2 = now_ms();

    // out: w=8400 (anchors), h=56 (channels)。YOLOv8-pose 输出布局:
    // ch0..3 = cx,cy,bw,bh (letterbox 尺度像素); ch4 = person score(已 sigmoid 或 logit);
    // ch5..55 = 17*(x,y,vis)。ncnn 此模型输出层末端已做 sigmoid(score) 与 kpt 拼接,
    // 故此处对 score 不再重复 sigmoid;若为 logit 需按需调整(见静态验证阶段核对)。
    const int num_anchors = out.w;   // 8400
    const int ch = out.h;            // 56
    std::vector<Person> cands;
    cands.reserve(64);

    // 通道行指针
    auto row = [&](int c) { return out.row(c); };

    for (int a = 0; a < num_anchors; ++a) {
        float score = row(4)[a];
        if (score < cfg_.conf_thresh) continue;

        float cx = row(0)[a];
        float cy = row(1)[a];
        float bw = row(2)[a];
        float bh = row(3)[a];
        // letterbox 尺度 -> 去 padding -> 除 scale -> 原图
        float x0 = (cx - bw / 2.f - left) / scale;
        float y0 = (cy - bh / 2.f - top)  / scale;
        float x1 = (cx + bw / 2.f - left) / scale;
        float y1 = (cy + bh / 2.f - top)  / scale;
        // 裁剪
        x0 = std::max(0.f, std::min(x0, (float)w - 1));
        y0 = std::max(0.f, std::min(y0, (float)h - 1));
        x1 = std::max(0.f, std::min(x1, (float)w - 1));
        y1 = std::max(0.f, std::min(y1, (float)h - 1));

        Person p;
        p.score = score;
        p.box = Rect{x0, y0, x1 - x0, y1 - y0};

        for (int k = 0; k < NUM_KPT; ++k) {
            float kx = row(5 + k * 3 + 0)[a];
            float ky = row(5 + k * 3 + 1)[a];
            float kv = row(5 + k * 3 + 2)[a];
            p.kpt[k].x = (kx - left) / scale;
            p.kpt[k].y = (ky - top)  / scale;
            p.kpt[k].conf = kv;   // 已 sigmoid
            // 关键点也裁剪到图像内
            p.kpt[k].x = std::max(0.f, std::min(p.kpt[k].x, (float)w - 1));
            p.kpt[k].y = std::max(0.f, std::min(p.kpt[k].y, (float)h - 1));
        }
        cands.push_back(std::move(p));
    }

    nms_sorted(cands, cfg_.nms_thresh);

    for (auto& p : cands) {
        assess_posture(p);
        estimate_face_region(p, w, h);
        persons.push_back(std::move(p));
    }

    double t3 = now_ms();
    t.pre_ms   = t1 - t0;
    t.infer_ms = t2 - t1;
    t.post_ms  = t3 - t2;
    t.total_ms = t3 - t0;
    return 0;
}

}  // namespace rgpose
