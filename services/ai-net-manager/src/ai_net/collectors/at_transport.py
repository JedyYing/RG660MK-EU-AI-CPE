"""ATTransport —— Modem AT 通道抽象（设计文档 15.3 / 13）。

设计要点
- 多后端自动探测：atci_socket（首选：atcid 代理，天然串行化、多客户端安全）
  > ccci_at（MTK AP-MD AT 通道，次选）> mipc_cli（mipc_wan_cli 子进程，兜底）。
- 进程内 threading.Lock + 跨进程 flock(lock_file) 双重互斥，避免本服务并发交错。
- 每条命令：echo 清理、OK/ERROR/CME ERROR 终止识别、URC 行单独收集、硬超时。
- 原始回包完整返回（落盘由上层 storage 决定）。
- 硬性护栏：禁止流式上报类命令（AT+ECELLMEAS=<非0> / AT+ECELL=<非0>）。
  2026-10-08 现场事故：开启流式测量后设备数分钟内失联重启（详见 reports/）。
"""
from __future__ import annotations

import fcntl
import os
import re
import select
import socket
import subprocess
import termios
import threading
import time
from dataclasses import dataclass, field

# 期望响应前缀提取：AT+ECELLMEAS? -> +ECELLMEAS
_CMD_NAME_RE = re.compile(r"^AT\+([A-Z0-9_]+)")

# 流式上报类命令：只允许查询(?)或关闭(=0)，其余一律拒绝
_STREAM_CMDS = ("ECELLMEAS", "ECELL")
_STREAM_BAD_RE = re.compile(r"^AT\+(" + "|".join(_STREAM_CMDS) + r")=(?!0\s*$)")

DEFAULT_TIMEOUT_S = 6.0


@dataclass
class ATResponse:
    ok: bool = False
    lines: list = field(default_factory=list)   # 响应行（不含 echo/OK）
    urc: list = field(default_factory=list)     # 无关 URC 行（如 +EDMFAPP）
    raw: str = ""
    error: str | None = None                    # "timeout" / "CME ERROR: 4" / "no backend" ...
    elapsed_ms: int = 0
    backend: str = ""
    cmd: str = ""

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class BackendProbe:
    name: str
    ok: bool
    elapsed_ms: int = 0
    detail: str = ""


class _Backend:
    name = "base"

    def send(self, cmd: str, timeout: float) -> ATResponse:  # pragma: no cover - interface
        raise NotImplementedError


class AtciSocketBackend(_Backend):
    """通过 /dev/adb_atci_socket（atcid 代理）发送原始 AT。每条命令独立连接。"""

    name = "atci_socket"

    def __init__(self, path: str = "/dev/adb_atci_socket"):
        self.path = path

    def send(self, cmd: str, timeout: float) -> ATResponse:
        t0 = time.monotonic()
        resp = ATResponse(cmd=cmd, backend=self.name)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.25)
        try:
            s.connect(self.path)
            s.sendall((cmd + "\r\n").encode())
            buf = b""
            term = None
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data = s.recv(8192)
                except socket.timeout:
                    if term:
                        break
                    continue
                if not data:
                    break
                buf += data
                term = _find_terminator(buf)
                if term:
                    break
            resp.raw = buf.decode(errors="replace")
            _classify(resp, cmd)
            if not term and not resp.ok:
                resp.error = resp.error or "timeout"
        except OSError as e:
            resp.error = "socket: %s" % e
        finally:
            try:
                s.close()
            except OSError:
                pass
        resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return resp


class CcciAtBackend(_Backend):
    """直连 /dev/ccci_at（MTK AP-MD AT 通道）。每条命令 open→drain→send→read→close。"""

    name = "ccci_at"

    def __init__(self, path: str = "/dev/ccci_at"):
        self.path = path

    def send(self, cmd: str, timeout: float) -> ATResponse:
        t0 = time.monotonic()
        resp = ATResponse(cmd=cmd, backend=self.name)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            resp.error = "open: %s" % e
            resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
            return resp
        try:
            self._raw_mode(fd)
            self._drain(fd, 0.15)          # 丢弃上一条命令残留/URC
            os.write(fd, (cmd + "\r\n").encode())
            buf = b""
            term = None
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                r, _, _ = select.select([fd], [], [], 0.25)
                if not r:
                    if term:
                        break
                    continue
                try:
                    data = os.read(fd, 16384)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                term = _find_terminator(buf)
                if term:
                    break
            resp.raw = buf.decode(errors="replace")
            _classify(resp, cmd)
            if not term and not resp.ok:
                resp.error = resp.error or "timeout"
        except OSError as e:
            resp.error = "io: %s" % e
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return resp

    @staticmethod
    def _raw_mode(fd: int) -> None:
        try:
            a = termios.tcgetattr(fd)
            a[0] = 0                                  # iflag
            a[1] = 0                                  # oflag
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            a[3] = 0                                  # lflag：关 echo/ICANON
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, a)
        except termios.error:
            pass

    @staticmethod
    def _drain(fd: int, wait: float) -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            try:
                if not os.read(fd, 16384):
                    break
            except OSError:
                break


class MipcCliBackend(_Backend):
    """兜底：mipc_wan_cli --at_cmd <cmd>（进程开销大，仅在前两者不可用时使用）。"""

    name = "mipc_cli"

    def __init__(self, binary: str = "/usr/bin/mipc_wan_cli"):
        self.binary = binary

    def send(self, cmd: str, timeout: float) -> ATResponse:
        t0 = time.monotonic()
        resp = ATResponse(cmd=cmd, backend=self.name)
        try:
            p = subprocess.run([self.binary, "--at_cmd", cmd],
                               capture_output=True, timeout=timeout)
            resp.raw = (p.stdout + p.stderr).decode(errors="replace")
            _classify(resp, cmd)
            if not resp.ok and not resp.error:
                resp.error = "rc=%s" % p.returncode
        except subprocess.TimeoutExpired:
            resp.error = "timeout"
        except OSError as e:
            resp.error = "spawn: %s" % e
        resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return resp


def _find_terminator(buf: bytes):
    """扫描完整行，返回终止行（OK / ERROR / +CME ERROR / +CMS ERROR）或 None。"""
    txt = buf.decode(errors="replace")
    for ln in txt.split("\n"):
        ln = ln.strip()
        if ln == "OK":
            return ln
        if ln == "ERROR":
            return ln
        if ln.startswith("+CME ERROR") or ln.startswith("+CMS ERROR"):
            return ln
    return None


def _classify(resp: ATResponse, cmd: str) -> None:
    """把 raw 拆成 echo / 响应行 / URC 行，并判定 ok/error。"""
    m = _CMD_NAME_RE.match(cmd.strip())
    want = ("+" + m.group(1)) if m else None
    lines = [ln.strip() for ln in resp.raw.replace("\r", "").split("\n")]
    body = []
    for ln in lines:
        if not ln:
            continue
        if ln == cmd.strip() or ln.startswith(cmd.strip()):   # echo
            continue
        if ln == "OK":
            resp.ok = True
            continue
        if ln == "ERROR":
            resp.error = "ERROR"
            continue
        if ln.startswith("+CME ERROR") or ln.startswith("+CMS ERROR"):
            resp.error = ln
            continue
        if want and ln.startswith(want):
            body.append(ln)
        elif ln.startswith("+"):
            resp.urc.append(ln)                                # 其它 URC（+EDMFAPP 等）
        else:
            body.append(ln)
    resp.lines = body
    if resp.ok and resp.error:
        resp.ok = False                                        # OK 与 ERROR 并存视为失败


class ATTransport:
    """带互斥与后端自动选择的 AT 发送器。"""

    def __init__(self, backends: list | None = None, *, lock_file: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S, allow_streaming_modes: bool = False):
        self.backends: list[_Backend] = []
        specs = ["atci_socket", "ccci_at", "mipc_cli"] if backends is None else backends
        for spec in specs:
            be = self._make_backend(spec)
            if be is not None:
                self.backends.append(be)
        self.active: _Backend | None = None
        self.timeout_s = timeout_s
        self.allow_streaming_modes = allow_streaming_modes
        self.lock_file = lock_file
        self._tlock = threading.Lock()
        self._probe_results: list[BackendProbe] = []

    @staticmethod
    def _make_backend(spec) -> _Backend | None:
        if isinstance(spec, _Backend):
            return spec
        if isinstance(spec, dict):                              # {name: ..., path/bin: ...}
            name = spec.get("name") or spec.get("type")
            kw = {k: v for k, v in spec.items() if k not in ("name", "type")}
        else:
            name, kw = str(spec), {}
        if name == "atci_socket":
            return AtciSocketBackend(kw.get("path", "/dev/adb_atci_socket"))
        if name == "ccci_at":
            return CcciAtBackend(kw.get("path", "/dev/ccci_at"))
        if name == "mipc_cli":
            return MipcCliBackend(kw.get("binary", "/usr/bin/mipc_wan_cli"))
        return None

    # -- 互斥 -------------------------------------------------------------
    def _file_lock(self):
        if not self.lock_file:
            return _NullCtx()
        return _FlockCtx(self.lock_file)

    # -- 核心发送 ---------------------------------------------------------
    def send(self, cmd: str, timeout: float | None = None) -> ATResponse:
        cmd = cmd.strip()
        if _STREAM_BAD_RE.match(cmd) and not self.allow_streaming_modes:
            return ATResponse(cmd=cmd, ok=False,
                              error="blocked: streaming-mode command forbidden",
                              backend="guard")
        timeout = timeout or self.timeout_s
        with self._tlock, self._file_lock():
            order = ([self.active] if self.active else []) + \
                    [b for b in self.backends if b is not self.active]
            last = None
            for be in order:
                r = be.send(cmd, timeout)
                if r.error in ("timeout",) or r.error is None or r.ok:
                    # 能正常拿到响应（哪怕 CME ERROR）即认为后端可用
                    if r.error != "timeout" or r.ok:
                        self.active = be
                        return r
                last = r
            return last or ATResponse(cmd=cmd, ok=False, error="no backend",
                                      backend="none")

    def probe_all(self) -> list[BackendProbe]:
        """启动探测：逐个后端发 AT。结果供 capability 报告使用。"""
        out = []
        for be in self.backends:
            r = be.send("AT", min(self.timeout_s, 4.0))
            ok = r.ok or (r.error is None and r.raw.strip() != "")
            out.append(BackendProbe(be.name, ok, r.elapsed_ms,
                                    (r.error or r.raw.strip().replace("\n", " | "))[:160]))
            if ok and self.active is None:
                self.active = be
        self._probe_results = out
        return out

    @property
    def probe_results(self) -> list[BackendProbe]:
        return list(self._probe_results)


class _FlockCtx:
    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            if self.fd is not None:
                os.close(self.fd)
        return False


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
