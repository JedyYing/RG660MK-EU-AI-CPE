// app_camera.cpp — USB 摄像头(Logitech C270, libuvc/MJPEG)姿态+坐姿检测入口
// 阶段B/C 与持久服务共用。采集线程与推理线程解耦,只保留最新帧(丢积压)。
//
// 用法:
//   pose_camera <param> <bin> [--size N] [--threads N] [--fps F] [--frames N]
//     [--vid 0x046d] [--pid 0x0825] [--conf f] [--kpt f]
//     [--jsonl out.jsonl] [--stats out.csv] [--duration-sec S] [--smooth N]
//     [--alert-hold S] [--quiet]
//
// 说明: T930 为 4x 同构 Cortex-A55,不做「绑定大核」,仅设置线程数与 nice 优先级。
#include "pose_detector.h"

#include <libuvc/libuvc.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <algorithm>

#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"   // 用于 MJPEG(JPEG) 解码

using namespace rgpose;
using clock_t2 = std::chrono::steady_clock;

// ---- 最新帧缓冲(单槽,丢旧帧)----
struct FrameSlot {
    std::mutex m;
    std::vector<unsigned char> jpeg;  // 最新一帧 MJPEG 原始字节
    uint64_t seq = 0;
    bool has = false;
};
static FrameSlot g_slot;
static std::atomic<bool> g_running{true};
static std::atomic<uint64_t> g_dropped{0};

static void uvc_cb(uvc_frame_t* frame, void* /*u*/) {
    if (!frame || !frame->data || frame->data_bytes < 4) return;
    if (frame->frame_format != UVC_FRAME_FORMAT_MJPEG) return;
    const unsigned char* d = (const unsigned char*)frame->data;
    if (d[0] != 0xff || d[1] != 0xd8) return;  // JPEG SOI
    std::lock_guard<std::mutex> lk(g_slot.m);
    if (g_slot.has) g_dropped.fetch_add(1);     // 覆盖未消费的旧帧 => 计一次丢帧
    g_slot.jpeg.assign(d, d + frame->data_bytes);
    g_slot.seq++;
    g_slot.has = true;
}

static double pct(std::vector<double>& v, double p) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    double idx = p / 100.0 * (v.size() - 1);
    size_t lo = (size_t)idx; double frac = idx - lo;
    if (lo + 1 < v.size()) return v[lo] * (1 - frac) + v[lo + 1] * frac;
    return v[lo];
}

// 读取自身 RSS/VmHWM (KB)
static long read_vmhwm_kb() {
    FILE* f = fopen("/proc/self/status", "r");
    if (!f) return -1;
    char l[256]; long v = -1;
    while (fgets(l, sizeof(l), f)) {
        if (strncmp(l, "VmHWM:", 6) == 0) { sscanf(l + 6, "%ld", &v); break; }
    }
    fclose(f); return v;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "用法: %s <param> <bin> [--size N] [--threads N] [--fps F] "
                        "[--frames N] [--duration-sec S] [--vid 0xVVVV] [--pid 0xPPPP] "
                        "[--conf f] [--kpt f] [--jsonl out] [--stats csv] [--smooth N] [--quiet]\n",
                argv[0]);
        return 2;
    }
    std::string param = argv[1], bin = argv[2];
    Config cfg;
    int req_fps = 15, max_frames = 100, duration_sec = 0;
    int vid = 0x046d, pid = 0x0825;
    std::string jsonl, statscsv;
    bool quiet = false;
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char* d) { return (i + 1 < argc) ? argv[++i] : d; };
        if (a == "--size")    cfg.target_size = atoi(next("320"));
        else if (a == "--threads") cfg.num_threads = atoi(next("2"));
        else if (a == "--fps")     req_fps = atoi(next("15"));
        else if (a == "--frames")  max_frames = atoi(next("100"));
        else if (a == "--duration-sec") duration_sec = atoi(next("0"));
        else if (a == "--vid")     vid = (int)strtol(next("0x046d"), nullptr, 0);
        else if (a == "--pid")     pid = (int)strtol(next("0x0825"), nullptr, 0);
        else if (a == "--conf")    cfg.conf_thresh = atof(next("0.25"));
        else if (a == "--kpt")     cfg.kpt_thresh  = atof(next("0.40"));
        else if (a == "--smooth")  cfg.smooth_window = atoi(next("5"));
        else if (a == "--jsonl")   jsonl = next("");
        else if (a == "--stats")   statscsv = next("");
        else if (a == "--quiet")   quiet = true;
    }

    PoseDetector det;
    if (det.load(param, bin, cfg) != 0) { fprintf(stderr, "模型加载失败\n"); return 1; }
    if (!quiet) fprintf(stderr, "[INFO] 模型已加载 size=%d threads=%d\n",
                        cfg.target_size, cfg.num_threads);

    // ---- 打开 UVC 摄像头 ----
    uvc_context_t* ctx = nullptr; uvc_device_t* dev = nullptr;
    uvc_device_handle_t* devh = nullptr; uvc_stream_ctrl_t ctrl;
    if (uvc_init(&ctx, nullptr) < 0) { fprintf(stderr, "uvc_init 失败\n"); return 1; }
    if (uvc_find_device(ctx, &dev, vid, pid, nullptr) < 0) {
        fprintf(stderr, "[FAIL] 未找到摄像头 %04x:%04x\n", vid, pid); uvc_exit(ctx); return 1;
    }
    if (uvc_open(dev, &devh) < 0) { fprintf(stderr, "uvc_open 失败\n"); uvc_unref_device(dev); uvc_exit(ctx); return 1; }

    uvc_error_t r = uvc_get_stream_ctrl_format_size(devh, &ctrl, UVC_FRAME_FORMAT_MJPEG, 640, 480, req_fps);
    if (r < 0) { req_fps = 15; r = uvc_get_stream_ctrl_format_size(devh, &ctrl, UVC_FRAME_FORMAT_MJPEG, 640, 480, req_fps); }
    if (r < 0) { fprintf(stderr, "[FAIL] MJPEG 640x480 不支持\n"); uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx); return 1; }
    if (uvc_start_streaming(devh, &ctrl, uvc_cb, nullptr, 0) < 0) {
        fprintf(stderr, "[FAIL] uvc_start_streaming\n"); uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx); return 1;
    }
    if (!quiet) fprintf(stderr, "[PASS] 摄像头 MJPEG 640x480 @ %d fps 启动\n", req_fps);

    FILE* fj = jsonl.empty() ? nullptr : fopen(jsonl.c_str(), "a");

    // ---- 推理循环(主线程消费最新帧)----
    std::vector<double> infer_ms_v, total_ms_v;
    std::deque<int> smooth_bad;   // 最近 N 帧是否坐姿不良(多数表决)
    uint64_t processed = 0, last_seq = 0;
    long peak_vmhwm = 0;
    auto t_start = clock_t2::now();

    while (g_running) {
        if (duration_sec > 0) {
            auto el = std::chrono::duration_cast<std::chrono::seconds>(clock_t2::now() - t_start).count();
            if (el >= duration_sec) break;
        }
        if (duration_sec == 0 && (int)processed >= max_frames) break;

        // 取最新帧
        std::vector<unsigned char> jpeg; uint64_t seq = 0; bool has = false;
        { std::lock_guard<std::mutex> lk(g_slot.m);
          if (g_slot.has && g_slot.seq != last_seq) { jpeg = g_slot.jpeg; seq = g_slot.seq; has = true; g_slot.has = false; } }
        if (!has) { std::this_thread::sleep_for(std::chrono::milliseconds(5)); continue; }
        last_seq = seq;

        int W, H, Cc;
        unsigned char* rgb = stbi_load_from_memory(jpeg.data(), (int)jpeg.size(), &W, &H, &Cc, 3);
        if (!rgb) continue;

        std::vector<Person> persons; Timing t;
        det.infer_rgb(rgb, W, H, persons, t);
        stbi_image_free(rgb);
        processed++;
        infer_ms_v.push_back(t.infer_ms);
        total_ms_v.push_back(t.total_ms);
        long hw = read_vmhwm_kb(); if (hw > peak_vmhwm) peak_vmhwm = hw;

        // 时间平滑(多数表决):任一人坐姿不良即视为该帧 bad
        int bad = 0; for (auto& p : persons) if (!p.posture_issues.empty()) { bad = 1; break; }
        smooth_bad.push_back(bad);
        while ((int)smooth_bad.size() > cfg.smooth_window) smooth_bad.pop_front();
        int votes = 0; for (int v : smooth_bad) votes += v;
        bool smoothed_bad = votes * 2 > (int)smooth_bad.size();

        if (fj) {
            fprintf(fj, "{\"seq\":%llu,\"num_person\":%zu,\"smoothed_posture\":\"%s\","
                        "\"infer_ms\":%.2f,\"total_ms\":%.2f}\n",
                    (unsigned long long)seq, persons.size(),
                    smoothed_bad ? "坐姿不良" : "坐姿良好", t.infer_ms, t.total_ms);
            fflush(fj);
        }
        if (!quiet && (processed % 20 == 0)) {
            fprintf(stderr, "[%llu] persons=%zu infer=%.1fms total=%.1fms smoothed=%s dropped=%llu\n",
                    (unsigned long long)processed, persons.size(), t.infer_ms, t.total_ms,
                    smoothed_bad ? "BAD" : "OK", (unsigned long long)g_dropped.load());
        }
    }

    uvc_stop_streaming(devh);
    uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx);
    if (fj) fclose(fj);

    // ---- 统计 ----
    auto el_ms = std::chrono::duration_cast<std::chrono::milliseconds>(clock_t2::now() - t_start).count();
    double fps = processed > 0 && el_ms > 0 ? processed * 1000.0 / el_ms : 0;
    double infer_avg = 0; for (double v : infer_ms_v) infer_avg += v;
    if (!infer_ms_v.empty()) infer_avg /= infer_ms_v.size();
    double infer_p50 = pct(infer_ms_v, 50), infer_p95 = pct(infer_ms_v, 95);
    double total_p95 = pct(total_ms_v, 95);

    fprintf(stderr, "\n===== 摄像头测试统计 =====\n");
    fprintf(stderr, "size=%d threads=%d 处理帧=%llu 用时=%.1fs FPS=%.2f\n",
            cfg.target_size, cfg.num_threads, (unsigned long long)processed, el_ms / 1000.0, fps);
    fprintf(stderr, "推理时延 avg=%.1f P50=%.1f P95=%.1f ms  端到端 P95=%.1f ms\n",
            infer_avg, infer_p50, infer_p95, total_p95);
    fprintf(stderr, "峰值内存 VmHWM=%ld KB 丢帧=%llu\n",
            peak_vmhwm, (unsigned long long)g_dropped.load());

    if (!statscsv.empty()) {
        bool exist = false; { FILE* c = fopen(statscsv.c_str(), "r"); if (c) { exist = true; fclose(c); } }
        FILE* c = fopen(statscsv.c_str(), "a");
        if (c) {
            if (!exist) fprintf(c, "size,threads,frames,seconds,fps,infer_avg_ms,infer_p50_ms,infer_p95_ms,e2e_p95_ms,peak_vmhwm_kb,dropped\n");
            fprintf(c, "%d,%d,%llu,%.1f,%.2f,%.1f,%.1f,%.1f,%.1f,%ld,%llu\n",
                    cfg.target_size, cfg.num_threads, (unsigned long long)processed, el_ms / 1000.0,
                    fps, infer_avg, infer_p50, infer_p95, total_p95, peak_vmhwm,
                    (unsigned long long)g_dropped.load());
            fclose(c);
        }
    }
    return 0;
}
