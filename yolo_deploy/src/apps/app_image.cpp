// app_image.cpp — 静态图片推理入口(阶段A基线与设备一致性验证)
// 用法:
//   pose_image <param> <bin> <img> [--size N] [--threads N] [--conf f] [--kpt f]
//             [--json out.jsonl] [--draw out.png]
// 输出: 每张图一行 JSON(stdout 或 --json 文件),含人员框/17关键点/坐姿/人脸区域。
#include "pose_detector.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

using namespace rgpose;

static std::string json_escape(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else o += c;
    }
    return o;
}

static void draw_rect(unsigned char* rgb, int W, int H, const Rect& r,
                      unsigned char cr, unsigned char cg, unsigned char cb) {
    int x0 = (int)r.x, y0 = (int)r.y, x1 = (int)(r.x + r.w), y1 = (int)(r.y + r.h);
    x0 = std::max(0, std::min(x0, W - 1)); x1 = std::max(0, std::min(x1, W - 1));
    y0 = std::max(0, std::min(y0, H - 1)); y1 = std::max(0, std::min(y1, H - 1));
    for (int x = x0; x <= x1; ++x)
        for (int yy : {y0, y1}) { int i = (yy * W + x) * 3; rgb[i]=cr; rgb[i+1]=cg; rgb[i+2]=cb; }
    for (int y = y0; y <= y1; ++y)
        for (int xx : {x0, x1}) { int i = (y * W + xx) * 3; rgb[i]=cr; rgb[i+1]=cg; rgb[i+2]=cb; }
}

static void draw_pt(unsigned char* rgb, int W, int H, float px, float py) {
    int cx=(int)px, cy=(int)py;
    for (int dy=-2; dy<=2; ++dy) for (int dx=-2; dx<=2; ++dx) {
        int x=cx+dx, y=cy+dy;
        if (x>=0&&x<W&&y>=0&&y<H){int i=(y*W+x)*3; rgb[i]=255; rgb[i+1]=255; rgb[i+2]=0;}
    }
}

int main(int argc, char** argv) {
    if (argc < 4) {
        fprintf(stderr, "用法: %s <param> <bin> <img> [--size N] [--threads N] "
                        "[--conf f] [--kpt f] [--json out] [--draw out.png]\n", argv[0]);
        return 2;
    }
    std::string param = argv[1], bin = argv[2], img = argv[3];
    Config cfg;
    std::string json_out, draw_out;
    for (int i = 4; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char* d) { return (i + 1 < argc) ? argv[++i] : d; };
        if (a == "--size")    cfg.target_size = atoi(next("320"));
        else if (a == "--threads") cfg.num_threads = atoi(next("2"));
        else if (a == "--conf")    cfg.conf_thresh = atof(next("0.25"));
        else if (a == "--kpt")     cfg.kpt_thresh  = atof(next("0.40"));
        else if (a == "--nms")     cfg.nms_thresh  = atof(next("0.45"));
        else if (a == "--json")    json_out = next("");
        else if (a == "--draw")    draw_out = next("");
    }

    int W, H, C;
    unsigned char* pix = stbi_load(img.c_str(), &W, &H, &C, 3);  // 强制 RGB
    if (!pix) { fprintf(stderr, "无法读取图片: %s\n", img.c_str()); return 1; }

    PoseDetector det;
    if (det.load(param, bin, cfg) != 0) {
        fprintf(stderr, "模型加载失败: %s / %s\n", param.c_str(), bin.c_str());
        stbi_image_free(pix); return 1;
    }

    std::vector<Person> persons;
    Timing t;
    det.infer_rgb(pix, W, H, persons, t);

    // 组装 JSON 行
    std::string line = "{";
    line += "\"image\":\"" + json_escape(img) + "\",";
    line += "\"width\":" + std::to_string(W) + ",\"height\":" + std::to_string(H) + ",";
    line += "\"size\":" + std::to_string(cfg.target_size) + ",";
    line += "\"threads\":" + std::to_string(cfg.num_threads) + ",";
    char tb[256];
    snprintf(tb, sizeof(tb), "\"timing_ms\":{\"pre\":%.2f,\"infer\":%.2f,\"post\":%.2f,\"total\":%.2f},",
             t.pre_ms, t.infer_ms, t.post_ms, t.total_ms);
    line += tb;
    line += "\"num_person\":" + std::to_string(persons.size()) + ",";
    line += "\"persons\":[";
    for (size_t pi = 0; pi < persons.size(); ++pi) {
        const Person& p = persons[pi];
        if (pi) line += ",";
        char b[512];
        snprintf(b, sizeof(b),
                 "{\"score\":%.4f,\"box\":[%.1f,%.1f,%.1f,%.1f],",
                 p.score, p.box.x, p.box.y, p.box.w, p.box.h);
        line += b;
        line += "\"keypoints\":[";
        for (int k = 0; k < NUM_KPT; ++k) {
            if (k) line += ",";
            char kb[96];
            snprintf(kb, sizeof(kb), "[%.1f,%.1f,%.3f]", p.kpt[k].x, p.kpt[k].y, p.kpt[k].conf);
            line += kb;
        }
        line += "],";
        if (p.has_face) {
            char fb[160];
            snprintf(fb, sizeof(fb), "\"face_region\":[%.1f,%.1f,%.1f,%.1f],",
                     p.face_region.x, p.face_region.y, p.face_region.w, p.face_region.h);
            line += fb;
        } else {
            line += "\"face_region\":null,";
        }
        auto numf = [](float v, int prec) -> std::string {
            if (std::isnan(v)) return std::string("null");
            char t[48]; snprintf(t, sizeof(t), "%.*f", prec, v); return std::string(t);
        };
        line += "\"metrics\":{\"shoulder_tilt\":" + numf(p.shoulder_tilt, 1) +
                ",\"head_forward\":" + numf(p.head_forward, 2) +
                ",\"head_drop\":" + numf(p.head_drop, 2) +
                ",\"trunk_tilt\":" + numf(p.trunk_tilt, 1) + "},";
        line += "\"posture\":\"" + json_escape(p.posture_label) + "\"}";
    }
    line += "]}";

    if (!json_out.empty()) {
        FILE* f = fopen(json_out.c_str(), "a");
        if (f) { fprintf(f, "%s\n", line.c_str()); fclose(f); }
    }
    printf("%s\n", line.c_str());

    if (!draw_out.empty()) {
        for (auto& p : persons) {
            draw_rect(pix, W, H, p.box, 0, 200, 0);
            if (p.has_face) draw_rect(pix, W, H, p.face_region, 255, 120, 0);
            for (int k = 0; k < NUM_KPT; ++k)
                if (p.kpt[k].conf > cfg.kpt_thresh) draw_pt(pix, W, H, p.kpt[k].x, p.kpt[k].y);
        }
        stbi_write_png(draw_out.c_str(), W, H, 3, pix, W * 3);
    }

    stbi_image_free(pix);
    return 0;
}
