// camview.cpp — RG660MK 实时摄像头网页预览服务
// 用户态方案:libuvc(基于 libusb)直接从 USB 抓 C270 的 MJPEG 帧,
// 经内置 HTTP 服务以 multipart/x-mixed-replace 推给浏览器。
// 完全绕过内核 V4L2(设备固件未编 CONFIG_MEDIA_SUPPORT),纯 JPEG 直通,不解码。
//
// 用法: camview [--port 8090] [--width 640] [--height 480] [--fps 15]
//               [--vid 0x046d] [--pid 0x0825]
//               [--bind ADDR]... [--token-file PATH]
//
// 安全(2026-11-19 安全测试整改):
//   · 所有接口(页面、/snapshot、/stream 以及 404 路径)都需要运维令牌:
//       Authorization: Bearer <令牌>   或   Authorization: Basic base64(任意用户名:令牌)
//   · 令牌文件默认 /data/ai_cpe/secrets/ops_token,须存在、非空,且权限为 0600;
//     否则拒绝启动(在打开摄像头之前退出)。
//   · 默认只绑定 127.0.0.1(看板 8099 代理)与 192.168.1.1(局域网,笔记本侧脚本使用),
//     不绑定 0.0.0.0、不监听 WAN。可用 --bind 重复指定地址覆盖默认值。
//   · 令牌比较为常量时间;请求头读取有超时;未认证请求不进入任何业务分支。

#include <libuvc/libuvc.h>

#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

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

// ---- 运维令牌与鉴权 ----
static std::string g_token;                                   // 启动时读取，非空才允许任何请求
static const char* kDefaultTokenFile = "/data/ai_cpe/secrets/ops_token";

static std::string trim(const std::string& s) {
    size_t b = 0, e = s.size();
    while (b < e && std::isspace((unsigned char)s[b])) b++;
    while (e > b && std::isspace((unsigned char)s[e - 1])) e--;
    return s.substr(b, e - b);
}

// 读取令牌：文件须存在、为普通文件、非空，且组/其他用户没有任何权限
static bool load_token(const std::string& path, std::string& out, std::string& err) {
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) { err = "运维令牌文件不存在: " + path; return false; }
    if (!S_ISREG(st.st_mode)) { err = "运维令牌不是普通文件: " + path; return false; }
    if (st.st_mode & 077) { err = "运维令牌文件权限过宽（须 chmod 600）: " + path; return false; }
    FILE* fp = fopen(path.c_str(), "rb");
    if (!fp) { err = "无法读取运维令牌文件: " + path; return false; }
    std::string s;
    char rb[256];
    size_t rn;
    while ((rn = fread(rb, 1, sizeof(rb), fp)) > 0) s.append(rb, rn);
    fclose(fp);
    out = trim(s);
    if (out.empty()) { err = "运维令牌文件为空: " + path; return false; }
    return true;
}

// 常量时间比较：不因首个不同字节提前返回
static bool ct_equal(const std::string& a, const std::string& b) {
    unsigned char diff = (a.size() == b.size()) ? 0 : 1;
    size_t n = a.size() < b.size() ? a.size() : b.size();
    for (size_t i = 0; i < n; i++) diff |= (unsigned char)(a[i] ^ b[i]);
    return diff == 0;
}

// Base64 解码：忽略非法字符，遇到 '=' 结束
static std::string b64_decode(const std::string& in) {
    static const char tbl[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    unsigned int val = 0;
    int bits = -8;
    for (unsigned char c : in) {
        if (c == '=') break;
        if (c == 0) continue;
        const char* p = strchr(tbl, (int)c);
        if (!p) continue;
        val = ((val << 6) | (unsigned int)(p - tbl)) & 0xFFFFFFu;
        bits += 6;
        if (bits >= 0) {
            out.push_back((char)((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

// 在请求头中查找指定头（大小写不敏感），返回去除首尾空白后的值；没有则返回空串
static std::string find_header(const std::string& head, const char* name) {
    size_t nlen = strlen(name);
    size_t pos = head.find("\r\n");               // 跳过请求行
    while (pos != std::string::npos) {
        pos += 2;
        size_t eol = head.find("\r\n", pos);
        if (eol == std::string::npos || eol == pos) break;   // 空行 = 头结束
        if (eol - pos > nlen && strncasecmp(head.c_str() + pos, name, nlen) == 0 &&
            head[pos + nlen] == ':') {
            return trim(head.substr(pos + nlen + 1, eol - pos - nlen - 1));
        }
        pos = eol;
    }
    return "";
}

// 校验 Authorization：Bearer <令牌>，或 Basic（密码即令牌，用户名任意）
static bool authorized(const std::string& head) {
    if (g_token.empty()) return false;
    std::string authz = find_header(head, "Authorization");
    if (authz.empty()) return false;
    std::string got;
    if (strncasecmp(authz.c_str(), "Bearer ", 7) == 0) {
        got = trim(authz.substr(7));
    } else if (strncasecmp(authz.c_str(), "Basic ", 6) == 0) {
        std::string dec = b64_decode(trim(authz.substr(6)));
        size_t colon = dec.find(':');
        if (colon == std::string::npos) return false;
        got = dec.substr(colon + 1);
    } else {
        return false;
    }
    return ct_equal(got, g_token);
}

// 读取完整请求头（直到空行），上限 8KB，接收超时 5 秒
static bool read_head(int fd, std::string& head) {
    struct timeval tv{5, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    char buf[1024];
    while (head.find("\r\n\r\n") == std::string::npos) {
        if (head.size() >= 8192) return false;
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) return false;
        head.append(buf, (size_t)n);
    }
    struct timeval none{0, 0};                       // 进入业务分支后取消超时（/stream 长连接）
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &none, sizeof(none));
    return true;
}

// 从请求行取路径（去掉查询串）：形如 GET /stream?123 HTTP/1.1
static bool parse_path(const std::string& head, std::string& path) {
    size_t sp1 = head.find(' ');
    if (sp1 == std::string::npos) return false;
    size_t sp2 = head.find(' ', sp1 + 1);
    if (sp2 == std::string::npos) return false;
    path = head.substr(sp1 + 1, sp2 - sp1 - 1);
    size_t q = path.find('?');
    if (q != std::string::npos) path.resize(q);
    return true;
}

static void send_401(int fd) {
    std::string body = "{\"ok\": false, \"error\": \"unauthorized\"}";
    std::string resp = "HTTP/1.1 401 Unauthorized\r\n"
                       "WWW-Authenticate: Basic realm=\"rg660mk-camview\"\r\n"
                       "Content-Type: application/json; charset=utf-8\r\n"
                       "Content-Length: " + std::to_string(body.size()) + "\r\n"
                       "Cache-Control: no-store\r\nConnection: close\r\n\r\n" + body;
    send_all(fd, resp.data(), resp.size());
}

static void handle_client(int fd) {
    std::string head, path;
    if (!read_head(fd, head) || !parse_path(head, path)) { close(fd); return; }

    // 所有路径（含 404）都先鉴权：未认证请求不进入任何业务分支
    if (!authorized(head)) { send_401(fd); close(fd); return; }

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

// 为单个 IPv4 地址创建监听套接字；失败返回 -1（errno 保留）
static int open_listener(const std::string& ip, int port) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) return -1;
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, ip.c_str(), &addr.sin_addr) != 1) { errno = EINVAL; close(srv); return -1; }
    if (bind(srv, (sockaddr*)&addr, sizeof(addr)) < 0) { int e = errno; close(srv); errno = e; return -1; }
    if (listen(srv, 16) < 0) { int e = errno; close(srv); errno = e; return -1; }
    return srv;
}

int main(int argc, char** argv) {
    int port = 8090, width = 640, height = 480, fps = 15;
    int vid = 0x046d, pid = 0x0825;
    std::vector<std::string> binds;
    std::string token_file = kDefaultTokenFile;
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
        else if (a == "--bind" && i + 1 < argc) binds.push_back(argv[++i]);
        else if (a == "--token-file" && i + 1 < argc) token_file = argv[++i];
    }
    if (binds.empty()) binds = {"127.0.0.1", "192.168.1.1"};

    // ---- 令牌：缺失/权限过宽/为空则在打开摄像头之前拒绝启动 ----
    std::string terr;
    if (!load_token(token_file, g_token, terr)) {
        fprintf(stderr, "[FAIL] %s\n", terr.c_str());
        return 2;
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

    // ---- HTTP 服务:只绑定指定地址;某地址失败只告警,全部失败则收尾退出 ----
    std::vector<std::pair<int, std::string>> listeners;
    for (const auto& ip : binds) {
        int fd = open_listener(ip, port);
        if (fd < 0) {
            fprintf(stderr, "[WARN] 无法绑定 %s:%d: %s\n", ip.c_str(), port, strerror(errno));
            continue;
        }
        listeners.emplace_back(fd, ip);
    }
    if (listeners.empty()) {
        fprintf(stderr, "[FAIL] 没有可用的监听地址\n");
        uvc_stop_streaming(devh); uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx);
        return 1;
    }
    std::string urls;
    for (const auto& l : listeners) urls += " http://" + l.second + ":" + std::to_string(port) + "/";
    fprintf(stderr, "[OK] 预览服务已就绪（需运维令牌）:%s  (Ctrl+C 停止)\n", urls.c_str());

    // ---- 事件循环:poll 超时 500ms,以便及时响应 SIGINT/SIGTERM ----
    std::vector<pollfd> pfds;
    for (const auto& l : listeners) pfds.push_back(pollfd{l.first, POLLIN, 0});
    while (g_running) {
        int pr = poll(pfds.data(), pfds.size(), 500);
        if (pr <= 0) continue;
        for (auto& p : pfds) {
            if (!(p.revents & POLLIN)) continue;
            int cfd = accept(p.fd, nullptr, nullptr);
            if (cfd >= 0) std::thread(handle_client, cfd).detach();
        }
    }

    fprintf(stderr, "\n[..] 收尾中\n");
    for (const auto& l : listeners) close(l.first);
    uvc_stop_streaming(devh);
    uvc_close(devh); uvc_unref_device(dev); uvc_exit(ctx);
    fprintf(stderr, "[OK] 已停止\n");
    return 0;
}
