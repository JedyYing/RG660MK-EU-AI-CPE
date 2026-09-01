
import ctypes, os, glob, struct, time, sys

libc = ctypes.CDLL(None, use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int

USBDEVFS_CONTROL          = 0xc0185500
USBDEVFS_CLAIMINTERFACE   = 0x8004550f
USBDEVFS_RELEASEINTERFACE = 0x80045510
USBDEVFS_SETINTERFACE     = 0x80085504
USBDEVFS_SETCONFIGURATION = 0x80045505
USBDEVFS_SUBMITURB        = 0x8038550a
USBDEVFS_REAPURB         = 0x4008550c
USBDEVFS_URB_ISO_ASAP     = 0x02
USBDEVFS_RESET            = 0x5514

class CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]

class IsoPktDesc(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("status", ctypes.c_uint32)]

class USBURB(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("endpoint", ctypes.c_uint8),
                ("status", ctypes.c_int), ("flags", ctypes.c_uint32),
                ("buffer", ctypes.c_void_p), ("buffer_length", ctypes.c_int),
                ("actual_length", ctypes.c_int), ("start_frame", ctypes.c_int),
                ("number_of_packets", ctypes.c_int), ("error_count", ctypes.c_int),
                ("signr", ctypes.c_uint32), ("usercontext", ctypes.c_void_p)]

NP = 16
PKT = 3060
bufsize = NP * PKT

def find_webcam():
    for d in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            with open(d) as f: vid = f.read().strip()
            with open(d.replace("idVendor", "idProduct")) as f: pid = f.read().strip()
            if vid == "046d" and pid == "0825":
                base = os.path.dirname(d)
                with open(base + "/busnum") as f: bus = int(f.read().strip())
                with open(base + "/devnum") as f: dev = int(f.read().strip())
                return bus, dev
        except Exception:
            pass
    return None

def open_cam():
    bus, dev = find_webcam()
    f = os.open(f"/dev/bus/usb/{bus:03d}/{dev:03d}", os.O_RDWR)
    print(f"C270: bus{bus} dev{dev}", flush=True)
    return f, bus, dev

fd, bus, dev = open_cam()

def ctrl(bm, b, wv, wi, data, timeout=1500):
    if isinstance(data, int):
        buf = ctypes.create_string_buffer(max(data, 1))
    else:
        buf = ctypes.create_string_buffer(bytes(data))
    ct = CtrlTransfer(bm, b, wv, wi, len(buf), timeout, ctypes.cast(buf, ctypes.c_void_p))
    r = libc.ioctl(fd, USBDEVFS_CONTROL, ctypes.addressof(ct))
    if r < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return buf.raw[:len(buf)]

def ioctl_val(req, v):
    a = ctypes.c_uint(v)
    r = libc.ioctl(fd, req, ctypes.addressof(a))
    if r < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

def ioctl_buf(req, buf):
    r = libc.ioctl(fd, req, ctypes.cast(buf, ctypes.c_void_p))
    if r < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

def submit(buf):
    urb = USBURB()
    urb.type = 0
    urb.endpoint = 0x81
    urb.flags = USBDEVFS_URB_ISO_ASAP
    urb.buffer = ctypes.cast(buf, ctypes.c_void_p)
    urb.buffer_length = len(buf)
    urb.number_of_packets = NP
    descs = (IsoPktDesc * NP)()
    for k in range(NP):
        descs[k].length = PKT
    full = ctypes.create_string_buffer(ctypes.sizeof(USBURB) + ctypes.sizeof(descs))
    ctypes.memmove(full, ctypes.addressof(urb), ctypes.sizeof(USBURB))
    ctypes.memmove(ctypes.addressof(full) + ctypes.sizeof(USBURB), ctypes.addressof(descs), ctypes.sizeof(descs))
    r = libc.ioctl(fd, USBDEVFS_SUBMITURB, ctypes.addressof(full))
    if r < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return full, buf

def reap():
    ptr = ctypes.c_void_p(0)
    r = libc.ioctl(fd, USBDEVFS_REAPURB, ctypes.addressof(ptr))
    if r < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return ptr.value

# ---- 流程 ----
# 0. USB 复位, 彻底清状态 (复位后重枚举, 需重开设备)
libc.ioctl(fd, USBDEVFS_RESET, ctypes.c_void_p(0))
os.close(fd)
time.sleep(1.5)
fd, bus, dev = open_cam()
# 1-2. 配置 + 认领
ioctl_val(USBDEVFS_SETCONFIGURATION, 1)
ioctl_val(USBDEVFS_CLAIMINTERFACE, 1)
print("CONFIG+CLAIM OK")

# 3. 先激活 alt7 (3060B/µs 高带宽)
ioctl_buf(USBDEVFS_SETINTERFACE, (ctypes.c_uint * 2)(1, 7))
# 4. PROBE: 请求 MJPEG 640x480@30
probe = struct.pack("<HBB IHHHHH II", 0x0001, 2, 1, 333333, 0, 0, 1, 0, 0, 0, 0)
commit = struct.pack("<HBB IHHHHH II", 0x0000, 2, 1, 333333, 0, 0, 1, 0, 0, 0, 0)
ctrl(0x21, 0x01, 0x0100, 1, probe)
cur = ctrl(0xA1, 0x81, 0x0100, 1, 26)
print("PROBE 协商:", cur[:4].hex(), "(fmt={} frame={})".format(cur[2], cur[3]))
ctrl(0x21, 0x01, 0x0200, 1, commit)
print("COMMIT OK")

# 5. 端点稳定期, 然后一次性塞满 40 个 URB
time.sleep(0.3)
urblist = []
prefill_ok = 0
for _ in range(16):
    try:
        urblist.append(submit(ctypes.create_string_buffer(bufsize)))
        prefill_ok += 1
    except OSError:
        break
print(f"预填 {prefill_ok}/16 个 URB 入队", flush=True)

# 6. 收流循环
jpeg = bytearray()
in_frame = False
saved = 0
t_end = time.time() + 12
reap_count = 0
total = 0

while time.time() < t_end and saved < 2:
    ptr = reap()
    reap_count += 1
    full = buf = None
    for f, b in urblist:
        if ctypes.addressof(f) == ptr:
            full, buf = f, b
            break
    if full is None:
        print("!! 提交结构不匹配")
        break
    urb = USBURB.from_buffer(full)
    descs = (IsoPktDesc * NP).from_buffer(full, ctypes.sizeof(USBURB))
    got = b""
    for k in range(NP):
        ln = descs[k].length
        if ln > 0:
            pkt = buf.raw[k * PKT : k * PKT + ln]
            if len(pkt) > 12 and pkt[0] >= 12:
                got += pkt[12:]
    total += len(got)
    if reap_count <= 2:
        print(f"reap#{reap_count}: status={urb.status} 收到{len(got)}B", flush=True)
    # JPEG 组帧
    i = 0
    while i < len(got):
        if not in_frame:
            if got[i] == 0xFF and i + 1 < len(got) and got[i+1] == 0xD8:
                in_frame = True
                jpeg = bytearray()
                jpeg += got[i:i+2]
                i += 2
                continue
        else:
            jpeg += bytes([got[i]])
            if got[i] == 0xFF and i + 1 < len(got) and got[i+1] == 0xD9:
                jpeg += b"\xD9"
                ts = time.strftime("%Y%m%d_%H%M%S")
                path = f"/data/ai_cpe/hermes/home/photo_{ts}_{saved}.jpg"
                with open(path, "wb") as fh:
                    fh.write(bytes(jpeg))
                print(f"✓ 帧 {saved+1} 已保存: {path} ({len(jpeg)}B)")
                saved += 1
                in_frame = False
                i += 2
                continue
        i += 1
    # 重新提交
    urblist = [u for u in urblist if ctypes.addressof(u[0]) != ptr]
    try:
        urblist.append(submit(ctypes.create_string_buffer(bufsize)))
    except OSError as e:
        if reap_count <= 4:
            print(f"resubmit 失败: {e}")
        try:
            urblist.append(submit(ctypes.create_string_buffer(bufsize)))
        except OSError:
            pass

print(f"收流结束: {reap_count} 次 reap, 共 {total}B, 保存 {saved} 帧")
ioctl_val(USBDEVFS_RELEASEINTERFACE, 1)
os.close(fd)
