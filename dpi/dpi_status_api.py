#!/usr/bin/env python3
"""RG660MK 视频轻量 DPI — 只读状态查询接口 (供设备 Hermes 拉取)。

设计边界:
- 只读。绝不改动解析服务、FIFO、抓包或任何设备/网络配置。
- 数据来源唯一: 读取解析服务持续追加的 service_metrics.jsonl 尾部,聚合最近状态。
- 无第三方依赖 (仅标准库 http.server)。可安全先于/独立于 AI Service 运行。

端点:
  GET /health       -> 接口自身存活 (与被监控的 DPI 是否有流无关)
  GET /dpi/status   -> DPI 实时状态: 有无活跃视频流 / 累计 RTP 包 / 最近窗口指标 / 数据新鲜度

安全:
- 仅返回聚合状态,不回传原始 pcap、payload。
- 可选 bearer token: 设环境变量 DPI_STATUS_TOKEN 后, 请求需带 X-DPI-Status-Token 匹配。
- 建议绑到设备可达的 LAN IP (192.168.1.244),不对公网开放。
"""
from __future__ import annotations
import argparse, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"jsonl": "", "token": "", "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def now_wall() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def tail_lines(path: str, max_bytes: int = 65536) -> list[str]:
    """读文件尾部若干字节, 返回完整行列表 (丢弃可能不完整的首行)。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                raw = f.read()
                raw = raw.split(b"\n", 1)[1] if b"\n" in raw else raw
            else:
                raw = f.read()
    except OSError:
        return []
    return [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]


def aggregate() -> dict:
    """从 jsonl 尾部聚合出 DPI 当前状态。"""
    lines = tail_lines(STATE["jsonl"])
    if not lines:
        return {
            "ok": True,
            "dpi_service": "UNKNOWN",
            "stream_status": "NO_DATA",
            "detail": "指标文件为空或不可读; 解析服务可能未运行或尚未写入",
            "ts": now_wall(),
        }
    last_health = last_window = last_stream = last_service = None
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "health":
            last_health = obj
        elif t == "window":
            last_window = obj
        elif t == "stream":
            last_stream = obj
        elif t == "service":
            last_service = obj

    # 数据新鲜度: 最后一条 health 的墙钟距今多久
    fresh_sec = None
    if last_health and last_health.get("ts"):
        try:
            t_last = time.mktime(time.strptime(last_health["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
            fresh_sec = round(time.time() - t_last, 1)
        except (ValueError, OverflowError):
            fresh_sec = None

    stream_status = last_health.get("status") if last_health else "NO_DATA"
    # 若最近 health 超过 30s 没更新, 视解析服务为疑似停摆
    dpi_service = "RUNNING"
    if fresh_sec is not None and fresh_sec > 30:
        dpi_service = "STALE"
    elif last_service and last_service.get("event") == "stop":
        dpi_service = "STOPPED"

    out = {
        "ok": True,
        "dpi_service": dpi_service,          # RUNNING / STALE / STOPPED / UNKNOWN
        "stream_status": stream_status,      # ACTIVE_STREAM / NO_ACTIVE_STREAM
        "cum_rtp": last_health.get("cum_rtp") if last_health else None,
        "active_ssrcs": last_health.get("active_ssrcs") if last_health else None,
        "metrics_age_sec": fresh_sec,
        "last_health_ts": last_health.get("ts") if last_health else None,
        "ts": now_wall(),
    }
    if last_window:
        out["last_window"] = {
            "cum_rtp": last_window.get("cum_rtp"),
            "parser_compute_ms": last_window.get("parser_compute_ms"),
            "recv_to_metric_ms": last_window.get("recv_to_metric_ms"),
        }
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "DPIStatus/1.0"

    def _auth_ok(self) -> bool:
        if not STATE["token"]:
            return True
        return self.headers.get("X-DPI-Status-Token", "") == STATE["token"]

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "service": "dpi-status-api",
                             "started": STATE["started"], "ts": now_wall()})
            return
        if not self._auth_ok():
            self._send(401, {"ok": False, "error": {"code": "UNAUTHORIZED",
                             "message": "missing or wrong X-DPI-Status-Token"}})
            return
        if self.path.rstrip("/") == "/dpi/status":
            self._send(200, aggregate())
            return
        self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": self.path}})

    def log_message(self, *args):  # 静默默认访问日志
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="只读 DPI 状态查询接口")
    ap.add_argument("--jsonl", required=True, help="解析服务的 service_metrics.jsonl 路径")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址; 设备查询用 192.168.1.244")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    STATE["jsonl"] = a.jsonl
    STATE["token"] = os.environ.get("DPI_STATUS_TOKEN", "")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    sys.stderr.write(f"[dpi-status-api] listening on {a.host}:{a.port} jsonl={a.jsonl} "
                     f"token={'set' if STATE['token'] else 'none'}\n")
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
