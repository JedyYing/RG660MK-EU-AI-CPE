// face_runner.cpp — RG660MK 人脸识别 runner
// RetinaFace (mnet.25) 检测 + 对齐 + MobileFaceNet 特征提取
// 输入(stdin): {"version":1,"operation":"face","input":{"path":"/abs/x.jpg"},
//               "models":{"face_detect":{"path":"/abs/d.param"},"face_embed":{"path":"/abs/e.param"}}}
// 输出(stdout): {"ok":true,"result":{"faces":[{"bbox":[x0,y0,x1,y1],"score":..,"embedding":[..]}]}}
#include "net.h"
#include <stdio.h>
#include <stdlib.h>
#include <string>
#include <vector>
#include <algorithm>
#include <cmath>

#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"

struct FaceObject {
    float x0, y0, x1, y1;
    float lm[5][2];
    float prob;
};

// ---------------- 检测（来自 ncnn retinaface 示例） ----------------
static inline float inter_area(const FaceObject& a, const FaceObject& b) {
    float x0 = std::max(a.x0, b.x0), y0 = std::max(a.y0, b.y0);
    float x1 = std::min(a.x1, b.x1), y1 = std::min(a.y1, b.y1);
    if (x1 <= x0 || y1 <= y0) return 0.f;
    return (x1 - x0) * (y1 - y0);
}
static void qsort_desc(std::vector<FaceObject>& v, int l, int r) {
    int i = l, j = r; float p = v[(l+r)/2].prob;
    while (i <= j) {
        while (v[i].prob > p) i++;
        while (v[j].prob < p) j--;
        if (i <= j) { std::swap(v[i], v[j]); i++; j--; }
    }
    if (l < j) qsort_desc(v, l, j);
    if (i < r) qsort_desc(v, i, r);
}
static void nms(const std::vector<FaceObject>& v, std::vector<int>& picked, float th) {
    picked.clear();
    int n = (int)v.size();
    std::vector<float> area(n);
    for (int i = 0; i < n; i++) area[i] = (v[i].x1-v[i].x0)*(v[i].y1-v[i].y0);
    for (int i = 0; i < n; i++) {
        int keep = 1;
        for (size_t j = 0; j < picked.size(); j++) {
            float inter = inter_area(v[i], v[picked[j]]);
            float uni = area[i] + area[picked[j]] - inter;
            if (inter / uni > th) { keep = 0; break; }
        }
        if (keep) picked.push_back(i);
    }
}
static ncnn::Mat gen_anchors(int base, const ncnn::Mat& ratios, const ncnn::Mat& scales) {
    int nr = ratios.w, ns = scales.w;
    ncnn::Mat a; a.create(4, nr*ns);
    float cx = base*0.5f, cy = base*0.5f;
    for (int i = 0; i < nr; i++) {
        float ar = ratios[i];
        int rw = (int)round(base/sqrt(ar)), rh = (int)round(rw*ar);
        for (int j = 0; j < ns; j++) {
            float sw = rw*scales[j], sh = rh*scales[j];
            float* p = a.row(i*ns+j);
            p[0]=cx-sw*0.5f; p[1]=cy-sh*0.5f; p[2]=cx+sw*0.5f; p[3]=cy+sh*0.5f;
        }
    }
    return a;
}
static void gen_proposals(const ncnn::Mat& anchors, int stride, const ncnn::Mat& score_blob,
                          const ncnn::Mat& bbox_blob, const ncnn::Mat& lm_blob, float th, std::vector<FaceObject>& out) {
    int w = score_blob.w, h = score_blob.h;
    int na = anchors.h;
    for (int q = 0; q < na; q++) {
        const float* anchor = anchors.row(q);
        const ncnn::Mat score = score_blob.channel(q + na);
        const ncnn::Mat bbox = bbox_blob.channel_range(q*4, 4);
        const ncnn::Mat lm = lm_blob.channel_range(q*10, 10);
        float ay = anchor[1];
        float aw = anchor[2]-anchor[0], ah = anchor[3]-anchor[1];
        for (int i = 0; i < h; i++) {
            float ax = anchor[0];
            for (int j = 0; j < w; j++) {
                int idx = i*w + j;
                if (score[idx] >= th) {
                    float dx = bbox.channel(0)[idx], dy = bbox.channel(1)[idx];
                    float dw = bbox.channel(2)[idx], dh = bbox.channel(3)[idx];
                    float cx = ax + aw*0.5f, cy = ay + ah*0.5f;
                    float pcx = cx + aw*dx, pcy = cy + ah*dy;
                    float pw = aw*exp(dw), ph = ah*exp(dh);
                    FaceObject o;
                    o.x0 = pcx - pw*0.5f; o.y0 = pcy - ph*0.5f;
                    o.x1 = pcx + pw*0.5f; o.y1 = pcy + ph*0.5f;
                    for (int k = 0; k < 5; k++) {
                        o.lm[k][0] = cx + (aw+1)*lm.channel(2*k)[idx];
                        o.lm[k][1] = cy + (ah+1)*lm.channel(2*k+1)[idx];
                    }
                    o.prob = score[idx];
                    out.push_back(o);
                }
                ax += stride;
            }
            ay += stride;
        }
    }
}
static int detect(ncnn::Net& net, const ncnn::Mat& rgb, std::vector<FaceObject>& faces) {
    int w = rgb.w, h = rgb.h;
    fprintf(stderr, "detect: creating extractor, img %dx%d c=%d\n", w, h, rgb.c);
    ncnn::Extractor ex = net.create_extractor();
    ex.set_light_mode(true);
    fprintf(stderr, "detect: input data\n");
    int ret = ex.input("data", rgb);
    fprintf(stderr, "detect: input ret=%d\n", ret);
    if (ret != 0) { fprintf(stderr, "detect: input failed\n"); return -1; }
    std::vector<FaceObject> props;
    struct CFG { const char* s[3]; int stride; float sc[2]; };
    const CFG cfgs[3] = {
        {{"face_rpn_cls_prob_reshape_stride32","face_rpn_bbox_pred_stride32","face_rpn_landmark_pred_stride32"}, 32, {32.f,16.f}},
        {{"face_rpn_cls_prob_reshape_stride16","face_rpn_bbox_pred_stride16","face_rpn_landmark_pred_stride16"}, 16, {8.f,4.f}},
        {{"face_rpn_cls_prob_reshape_stride8","face_rpn_bbox_pred_stride8","face_rpn_landmark_pred_stride8"}, 8, {2.f,1.f}},
    };
    const float prob_th = 0.8f, nms_th = 0.4f;
    for (int c = 0; c < 3; c++) {
        fprintf(stderr, "detect: stride %d extracting...\n", cfgs[c].stride);
        ncnn::Mat sb, bb, lb;
        if (ex.extract(cfgs[c].s[0], sb) != 0 || ex.extract(cfgs[c].s[1], bb) != 0 || ex.extract(cfgs[c].s[2], lb) != 0) {
            fprintf(stderr, "extract failed at stride %d\n", cfgs[c].stride);
            continue;
        }
        if (sb.empty() || bb.empty() || lb.empty()) {
            fprintf(stderr, "empty blob at stride %d\n", cfgs[c].stride);
            continue;
        }
        fprintf(stderr, "stride %d: sb=%dx%d c=%d, bb=%dx%d c=%d, lb=%dx%d c=%d\n",
                cfgs[c].stride, sb.w, sb.h, sb.c, bb.w, bb.h, bb.c, lb.w, lb.h, lb.c);
        ncnn::Mat ratios(1); ratios[0] = 1.f;
        ncnn::Mat scales(2); scales[0]=cfgs[c].sc[0]; scales[1]=cfgs[c].sc[1];
        ncnn::Mat anchors = gen_anchors(16, ratios, scales);
        fprintf(stderr, "stride %d: anchors=%d\n", cfgs[c].stride, anchors.h);
        gen_proposals(anchors, cfgs[c].stride, sb, bb, lb, prob_th, props);
        fprintf(stderr, "stride %d: proposals=%zu\n", cfgs[c].stride, props.size());
    }
    if (props.empty()) return 0;
    qsort_desc(props, 0, (int)props.size()-1);
    std::vector<int> picked; nms(props, picked, nms_th);
    for (size_t i = 0; i < picked.size(); i++) {
        FaceObject o = props[picked[i]];
        o.x0 = std::max(std::min(o.x0, (float)w-1), 0.f);
        o.y0 = std::max(std::min(o.y0, (float)h-1), 0.f);
        o.x1 = std::max(std::min(o.x1, (float)w-1), 0.f);
        o.y1 = std::max(std::min(o.y1, (float)h-1), 0.f);
        faces.push_back(o);
    }
    return 0;
}

// ---------------- 对齐（insightface 5点相似变换 + warp） ----------------
static void sim_transform(const float* src, const float* dst, float* M) {
    float msx=0,msy=0,mdx=0,mdy=0;
    for (int i=0;i<5;i++){ msx+=src[2*i]; msy+=src[2*i+1]; mdx+=dst[2*i]; mdy+=dst[2*i+1]; }
    msx/=5; msy/=5; mdx/=5; mdy/=5;
    float a=0,b=0,den=0;
    for (int i=0;i<5;i++){
        float sx=src[2*i]-msx, sy=src[2*i+1]-msy;
        float dx=dst[2*i]-mdx, dy=dst[2*i+1]-mdy;
        a += sx*dx + sy*dy; b += sx*dy - sy*dx; den += sx*sx + sy*sy;
    }
    a/=den; b/=den;
    M[0]=a; M[1]=-b; M[2]=mdx-(a*msx-b*msy);
    M[3]=b; M[4]=a;  M[5]=mdy-(b*msx+a*msy);
}
static inline float bilinear(const float* d, int w, int h, float x, float y) {
    if (x < 0 || y < 0 || x >= w-1 || y >= h-1) return 0.f;
    int x0=(int)x, y0=(int)y, x1=x0+1, y1=y0+1;
    float fx=x-x0, fy=y-y0;
    float v00=d[y0*w+x0], v01=d[y0*w+x1], v10=d[y1*w+x0], v11=d[y1*w+x1];
    return (v00*(1-fx)+v01*fx)*(1-fy) + (v10*(1-fx)+v11*fx)*fy;
}
static ncnn::Mat warp(const ncnn::Mat& img, const float M[6], int ow, int oh) {
    float det = M[0]*M[4]-M[1]*M[3];
    float im[6];
    im[0]=M[4]/det; im[1]=-M[1]/det; im[2]=(M[1]*M[5]-M[2]*M[4])/det;
    im[3]=-M[3]/det; im[4]=M[0]/det; im[5]=(M[2]*M[3]-M[0]*M[5])/det;
    ncnn::Mat out; out.create(ow, oh, img.c);
    for (int y=0;y<oh;y++) for (int x=0;x<ow;x++) {
        float sx = im[0]*x + im[1]*y + im[2];
        float sy = im[3]*x + im[4]*y + im[5];
        for (int c=0;c<img.c;c++)
            out.channel(c)[y*ow+x] = bilinear((const float*)img.channel(c), img.w, img.h, sx, sy);
    }
    return out;
}
static ncnn::Mat align(const ncnn::Mat& rgb, const FaceObject& f) {
    static const float dst[10] = {38.2946f,51.6963f, 73.5318f,51.5014f, 56.0252f,71.7366f, 41.5493f,92.3655f, 70.7299f,92.2041f};
    float src[10];
    for (int i=0;i<5;i++){ src[2*i]=f.lm[i][0]; src[2*i+1]=f.lm[i][1]; }
    float M[6]; sim_transform(src, dst, M);
    return warp(rgb, M, 112, 112);
}

// ---------------- 特征提取（MobileFaceNet, data->fc1 128维） ----------------
static int embed(ncnn::Net& net, const ncnn::Mat& rgb112, std::vector<float>& feat) {
    ncnn::Extractor ex = net.create_extractor();
    ex.set_light_mode(true);
    ex.input("data", rgb112);
    ncnn::Mat out;
    if (ex.extract("fc1", out) != 0) return -1;
    feat.resize(out.w * out.h * out.c);
    for (size_t i = 0; i < feat.size(); i++) feat[i] = out[i];
    return 0;
}

// ---------------- 极简 JSON 提取 ----------------
static std::string json_path(const std::string& s, const std::string& anchor) {
    size_t pos = s.find("\"" + anchor + "\"");
    if (pos == std::string::npos) return "";
    size_t p = s.find("\"path\"", pos);
    if (p == std::string::npos) return "";
    p = s.find("\"", p + 6);
    if (p == std::string::npos) return "";
    size_t e = s.find("\"", p + 1);
    if (e == std::string::npos) return "";
    return s.substr(p + 1, e - p - 1);
}
static std::string bin_of(const std::string& param) {
    std::string b = param;
    size_t p = b.rfind(".param");
    if (p != std::string::npos) b.replace(p, 6, ".bin");
    return b;
}

int main() {
    std::string req, line;
    char buf[65536];
    while (fgets(buf, sizeof(buf), stdin)) req += buf;
    std::string img_path = json_path(req, "input");
    std::string fd_param = json_path(req, "face_detect");
    std::string fe_param = json_path(req, "face_embed");
    fprintf(stderr, "img=%s fd=%s fe=%s\n", img_path.c_str(), fd_param.c_str(), fe_param.c_str());
    if (img_path.empty() || fd_param.empty() || fe_param.empty()) {
        printf("{\"ok\":false,\"error\":\"bad request\"}\n"); return 1;
    }

    ncnn::Net detnet, embnet;
    detnet.opt.use_vulkan_compute = false;
    embnet.opt.use_vulkan_compute = false;
    detnet.opt.num_threads = 2;
    embnet.opt.num_threads = 2;
    fprintf(stderr, "loading det param: %s\n", fd_param.c_str());
    if (detnet.load_param(fd_param.c_str()) != 0) {
        printf("{\"ok\":false,\"error\":\"load face_detect param failed\"}\n"); return 1;
    }
    fprintf(stderr, "loading det model: %s\n", bin_of(fd_param).c_str());
    if (detnet.load_model(bin_of(fd_param).c_str()) != 0) {
        printf("{\"ok\":false,\"error\":\"load face_detect model failed\"}\n"); return 1;
    }
    fprintf(stderr, "loading emb param: %s\n", fe_param.c_str());
    if (embnet.load_param(fe_param.c_str()) != 0) {
        printf("{\"ok\":false,\"error\":\"load face_embed param failed\"}\n"); return 1;
    }
    fprintf(stderr, "loading emb model: %s\n", bin_of(fe_param).c_str());
    if (embnet.load_model(bin_of(fe_param).c_str()) != 0) {
        printf("{\"ok\":false,\"error\":\"load face_embed model failed\"}\n"); return 1;
    }
    fprintf(stderr, "models loaded\n");

    int w, h, ch;
    unsigned char* px = stbi_load(img_path.c_str(), &w, &h, &ch, 3);
    if (!px) { printf("{\"ok\":false,\"error\":\"imread failed\"}\n"); return 1; }
    fprintf(stderr, "image loaded: %dx%d ch=%d\n", w, h, ch);
    ncnn::Mat rgb = ncnn::Mat::from_pixels(px, ncnn::Mat::PIXEL_RGB, w, h);
    stbi_image_free(px);

    // RetinaFace 需要 mean subtraction
    const float mean_vals[3] = {104.f, 117.f, 123.f};
    const float norm_vals[3] = {1.f, 1.f, 1.f};
    rgb.substract_mean_normalize(mean_vals, norm_vals);
    fprintf(stderr, "input normalized\n");

    std::vector<FaceObject> faces;
    fprintf(stderr, "running detect\n");
    detect(detnet, rgb, faces);
    fprintf(stderr, "detect done: %zu faces\n", faces.size());

    printf("{\"ok\":true,\"result\":{\"faces\":[");
    for (size_t i = 0; i < faces.size(); i++) {
        ncnn::Mat aligned = align(rgb, faces[i]);
        std::vector<float> feat;
        embed(embnet, aligned, feat);
        if (i) printf(",");
        printf("{\"bbox\":[%.2f,%.2f,%.2f,%.2f],\"score\":%.4f,\"embedding\":[",
               faces[i].x0, faces[i].y0, faces[i].x1, faces[i].y1, faces[i].prob);
        for (size_t k = 0; k < feat.size(); k++) printf("%s%.6f", k?",":"", feat[k]);
        printf("]}");
    }
    printf("],\"count\":%d}}\n", (int)faces.size());
    return 0;
}
