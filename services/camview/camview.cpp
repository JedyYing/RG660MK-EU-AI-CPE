// camview.cpp — RG660MK 实时摄像头网页预览服务
// 用户态方案:libuvc(基于 libusb)直接从 USB 抓 C270 的 MJPEG 帧,
// 经内置 HTTP 服务以 multipart/x-mixed-replace 推给浏览器。
// 完全绕过内核 V4L2(设备固件未编 CONFIG_MEDIA_SUPPORT),纯 JPEG 直通,不解码。
//
// 用法: camview [--port 8090] [--width 640] [--height 480] [--fps 15]
//               [--vid 0x046d] [--pid 0x0825]
//   默认绑 0.0.0.0,局域网内 http://192.168.1.1:8090 即可预览。

#include <libuvc/libuvc.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <chrono>
#include <csignal>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>

// ---- 最新帧缓冲(单槽,丢旧帧)----
struct FrameSlot {
    std::mutex m;
    std::vector<unsigned char> jpeg;
    uint64_t seq = 0;
    bool has = false;
};
static FrameSlot g_slot;
static std::atomic<bool> g_running{true};
static std::atomic<uint64_t> g_frames{0};

static void on_sig(int) { g_running = false; }

static void uvc_cb(uvc_frame_t* frame, void*) {
    if (!frame || !frame->data || frame->data_bytes < 4) return;
    if (frame->frame_format != UVC_FRAME_FORMAT_MJPEG) return;
    const unsigned char* d = (const unsigned char*)frame->data;
    if (d[0] != 0xff || d[1] != 0xd8) return;  // JPEG SOI
    std::lock_guard<std::mutex> lk(g_slot.m);
    g_slot.jpeg.assign(d, d + frame->data_bytes);
    g_slot.seq++;
    g_slot.has = true;
    g_frames.fetch_add(1);
}

// 取最新帧(拷贝出来);返回 seq,0 表示暂无
static uint64_t grab(std::vector<unsigned char>& out) {
    std::lock_guard<std::mutex> lk(g_slot.m);
    if (!g_slot.has) return 0;
    out = g_slot.jpeg;
    return g_slot.seq;
}

static const char* INDEX_HTML =
"<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
"<title>RG660MK 摄像头预览</title><style>"
"*{box-sizing:border-box;margin:0;padding:0}"
"body{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#0f1115;"
"color:#e6e8ec;min-height:100vh;display:flex;flex-direction:column;align-items:center;"
"padding:20px;gap:14px}"
"h1{font-size:19px;font-weight:600}"
".sub{color:#8b8f98;font-size:13px}"
"#stage{width:100%;max-width:900px;aspect-ratio:4/3;background:#000;border-radius:12px;"
"overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center}"
"#stage img{width:100%;height:100%;object-fit:contain}"
".bar{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}"
"a.btn,button{font-size:14px;padding:9px 16px;border-radius:8px;border:1px solid #2b2f36;"
"background:#1a1d23;color:#e6e8ec;cursor:pointer;text-decoration:none}"
"a.btn.p{background:#2f6feb;border-color:#2f6feb;color:#fff}"
"</style></head><body>"
"<h1>RG660MK 实时摄像头预览</h1>"
"<div class=\"sub\">Logitech C270 · libuvc 用户态直采 · MJPEG</div>"
"<div id=\"stage\"><img id=\"v\" src=\"/stream\" alt=\"camera\"></div>"
"<div class=\"bar\">"
"<a class=\"btn p\" href=\"/stream\" target=\"_blank\">全屏视频流</a>"
"<a class=\"btn\" href=\"/snapshot\" download=\"snapshot.jpg\">截图保存</a>"
"<button onclick=\"document.getElementById('v').src='/stream?'+Date.now()\">重连画面</button>"
"</div>"
"<div class=\"sub\">画面全黑说明镜头无进光或被遮挡;帧率随光照自动变化(C270 特性)。</div>"
"</body></html>";

static void send_all(int fd, const char* p, size_t n) {
    while (n > 0) {
        ssize_t w = send(fd, p, n, MSG_NOSIGNAL);
        if (w <= 0) return;
        p += w; n -= (size_t)w;
    }
}

// 读一行(丢弃),简单跳过请求头
static bool read_request(int fd, std::string& path) {
    char buf[2048]; int n = recv(fd, buf, sizeof(buf) - 1, 0);
    if (n <= 0) return false;
    buf[n] = 0;
    // 形如: GET /stream?123 HTTP/1.1
    char* sp1 = strchr(buf, ' ');
    if (!sp1) return false;
    char* sp2 = strchr(sp1 + 1, ' ');
    if (!sp2) return false;
    *sp2 = 0;
    path.assign(sp1 + 1);
    // 去掉查询串
    size_t q = path.find('?');
    if (q != std::string::npos) path.resize(q);
    return true;
}

static void handle_client(int fd) {
    std::string path;
    if (!read_request(fd, path)) { close(fd); return; }

    if (path == "/" || path == "/index.html") {
        std::string body = INDEX_HTML;
        char hdr[256];
        int hn = snprintf(hdr, sizeof(hdr),
            "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            "Content-Length: %zu\r\nConnection: close\r\n\r\n", body.size());
        send_all(fd, hdr, hn);
        send_all(fd, body.data(), body.size());
        close(fd);
        return;
    }

    if (path == "/snapshot") {
        std::vector<unsigned char> f;
        // 最多等 2 秒拿到一帧
        for (int i = 0; i < 40 && grab(f) == 0 && g_running; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        if (f.empty()) {
            const char* msg = "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 11\r\nConnection: close\r\n\r\nno frame yet";
            send_all(fd, msg, strlen(msg));
            close(fd);
            return;
        }
        char hdr[256];
        int hn = snprintf(hdr, sizeof(hdr),
            "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
            "Content-Length: %zu\r\nConnection: close\r\n\r\n", f.size());
        send_all(fd, hdr, hn);
        send_all(fd, (const char*)f.data(), f.size());
        close(fd);
        return;
    }

    if (path == "/stream") {
        const char* hdr =
            "HTTP/1.1 200 OK\r\n"
            "Cache-Control: no-cache, private\r\n"
            "Pragma: no-cache\r\n"
            "Connection: close\r\n"
            "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
        send_all(fd, hdr, strlen(hdr));
        uint64_t last = 0;
        std::vector<unsigned char> f;
        while (g_running) {
            uint64_t s = grab(f);
            if (s == 0 || s == last) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            last = s;
            char part[128];
            int pn = snprintf(part, sizeof(part),
                "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %zu\r\n\r\n", f.size());
            send_all(fd, part, pn);
            send_all(fd, (const char*)f.data(), f.size());
            send_all(fd, "\r\n", 2);
            // send 失败会在 send_all 内部静默返回;用一次探测判断对端是否断开
            char probe;
            int r = recv(fd, &probe, 1, MSG_DONTWAIT);
            if (r == 0) break;  // 对端关闭
        }
        close(fd);
        return;
    }

    const char* nf = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nnot found";
    send_all(fd, nf, strlen(nf));
    close(fd);
}

int main(int argc, char** argv) {
    int port = 8090, width = 640, height = 480, fps = 15;
    int vid = 0x046d, pid = 0x0825;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto next = [&](int def) { return (i + 1 < argc) ? atoi(argv[++i]) : def; };
        auto nexthex = [&](int def) { return (i + 1 < argc) ? (int)strtol(argv[++i], nullptr, 0) : def; };
        if (a == "--port") port = next(port);
        else if (a == "--width") width = next(width);
        else if (a == "--height") height = next(height);
        else if (a == "--fps") fps = next(fps);
        else if (a == "--vid") vid = nexthex(vid);
        else if (a == "--pid") pid = nexthex(pid);
    }

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    signal(SIGPIPE, SIG_IGN);

    // ---- 启动 UVC 采集 ----
    uvc_context_t* ctx = nullptr; uvc_device_t* dev = nullptr;
    uvc_device_handle_t* devh = nullptr; uvc_stream_ctrl_t ctrl;
    if (uvc_init(&ctx, nullptr) < 0) { fprintf(stderr, "uvc_init 失败\n"); return 1; }
    if (uvc_find_device(ctx, &dev, vid, pid, nullptr) < 0) {
        fprintf(stderr, "[FAIL] 未找到摄像头 %04x:%04x\n", vid, pid); uvc_exit(ctx); return 1;
    }
    if (uvc_open(dev, &devh) < 0) {
        fprintf(stderr, "[FAIL] uvc_open 失败(设备被占用?)\n");
        uvc_unref_device(dev); uvc_exit(ctx); return 1;
    }
    uvc_error_t r = uvc_get_stream_ctrl_format_size(devh, &ctrl, UVC_FRAME_FORMAT_MJPEG, width, height, fps);
    if (r < 0) {
        fprintf(stderr, "[WARN] MJPEG %dx%d@%d 不支持,回退 640x480@15\n", width, height, fps);
        width = 640; height = 480; fps = 15;
        r = uvc_get_stream_ctrl_format_size(devh, &ctrl, UVC_FRAME_FORMAT_MJPEG, width, height, fps);
    }
    if (r < 0) {
        fprintf(stderr, "[FAIL] MJPEG 格式协商失败\n");
        uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx); return 1;
    }
    if (uvc_start_streaming(devh, &ctrl, uvc_cb, nullptr, 0) < 0) {
        fprintf(stderr, "[FAIL] uvc_start_streaming 失败\n");
        uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx); return 1;
    }
    fprintf(stderr, "[OK] UVC 采集已启动 %dx%d@%dfps MJPEG (%04x:%04x)\n", width, height, fps, vid, pid);

    // ---- HTTP 服务 ----
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror("socket"); return 1; }
    int one = 1; setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{}; addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY; addr.sin_port = htons(port);
    if (bind(srv, (sockaddr*)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "[FAIL] bind %d 失败: %s\n", port, strerror(errno));
        return 1;
    }
    if (listen(srv, 16) < 0) { perror("listen"); return 1; }
    fprintf(stderr, "[OK] 预览服务已就绪: http://0.0.0.0:%d/  (Ctrl+C 停止)\n", port);

    while (g_running) {
        sockaddr_in cli{}; socklen_t cl = sizeof(cli);
        int fd = accept(srv, (sockaddr*)&cli, &cl);
        if (fd < 0) { if (g_running) continue; else break; }
        std::thread(handle_client, fd).detach();
    }

    fprintf(stderr, "\n[..] 收尾中\n");
    close(srv);
    uvc_stop_streaming(devh);
    uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx);
    fprintf(stderr, "[OK] 已停止\n");
    return 0;
}
