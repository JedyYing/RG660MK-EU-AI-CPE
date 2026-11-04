"""统一大模型通道：本地(MT200 NPU) / 在线(Hermes 在线 DeepSeek)。

模式唯一来源：/data/ai_cpe/local-model-mode.json（model_mode.py 读写）
  mt200-local → 本地模式：http://192.168.1.244:18091/v1/chat/completions
                （笔记本 LAN 代理 → SSH 隧道 → MT200:8090，模型 deepseek-7b-npu）
  hermes      → 在线模式：https://ai.phicotek.com/api/llmrouter/v1/chat/completions
                （与设备上的 Hermes 同一 provider/同一 key，模型 deepseek-v4-flash）

安全约束：
  - 端点固定，禁止代理、禁止重定向；
  - 在线 key 只从设备 /data/hermes/.hermes/.env 读取，不写日志、不回传页面；
  - 调用方拿到 (text, meta)，meta 里只有 backend/model/elapsed_ms 这类可展示信息。

对外接口（保持与旧 local_llm 兼容）：
  ask(text) -> str
  ask_meta(text) -> (str, meta)
  chat(messages) -> str
  chat_meta(messages, max_tokens=None, timeout=None) -> (str, meta)
  describe() -> dict          # 给看板展示
  allowed_hosts() -> set      # 给看板的 HTTP 白名单用
  ModelError / LocalModelError（别名）
"""
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MODE_FILE = '/data/ai_cpe/local-model-mode.json'
ENV_FILE = '/data/hermes/.hermes/.env'

LOCAL_ENDPOINT = 'http://192.168.1.244:18091/v1/chat/completions'
LOCAL_MODEL = 'deepseek-7b-npu'
LOCAL_HOST = '192.168.1.244'
LOCAL_HOSTS = ['192.168.1.244', '172.25.25.240']
_LOCAL_PICK = {'url': None, 't': 0.0}

ONLINE_ENDPOINT = 'https://ai.phicotek.com/api/llmrouter/v1/chat/completions'
ONLINE_MODEL = 'deepseek-v4-flash'
ONLINE_HOST = 'ai.phicotek.com'
ONLINE_KEY_NAME = 'PHICOTEK_API_KEY'

MODES = ('mt200-local', 'hermes')
ALIASES = {'local': 'mt200-local', 'mt200-local': 'mt200-local',
           'online': 'hermes', 'hermes': 'hermes', 'cloud': 'hermes'}
LABELS = {'mt200-local': '本地模式', 'hermes': '在线模式'}

# 本地模型 1024 token 上下文，短输入保守限额；在线模式可放宽。
LOCAL_CHAR_LIMIT = 600
LOCAL_MAX_TOKENS = 256
ONLINE_CHAR_LIMIT = 4000
ONLINE_MAX_TOKENS = 1024


class ModelError(RuntimeError):
    pass


# 兼容旧名
LocalModelError = ModelError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError('禁止重定向')


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _env_key(name=ONLINE_KEY_NAME):
    key = os.environ.get(name, '')
    if key:
        return key.strip()
    try:
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + '='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ''


def normalize_mode(mode):
    m = ALIASES.get(str(mode or '').strip())
    if not m:
        raise ModelError('未知模式：%s' % str(mode)[:20])
    return m


def get_mode():
    """读模式文件；文件缺失=在线(hermes，历史默认)；内容非法=报错，不静默切换通道。"""
    try:
        with open(MODE_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return 'hermes'
    except (OSError, ValueError) as exc:
        raise ModelError('模式配置无效，停止处理，不自动切换通道') from exc
    if not isinstance(data, dict) or set(data) != {'mode'} or data['mode'] not in MODES:
        raise ModelError('模式配置无效，停止处理，不自动切换通道')
    return data['mode']


def set_mode(mode):
    mode = normalize_mode(mode)
    fd, tmp = None, None
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix='.model-mode-', dir=os.path.dirname(MODE_FILE))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'mode': mode}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, MODE_FILE)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return mode


def allowed_hosts():
    hosts = {'127.0.0.1', 'localhost', '::1', '192.168.1.1', '192.168.1.244', '172.25.25.240'}
    try:
        if get_mode() == 'hermes':
            hosts.add(ONLINE_HOST)
    except ModelError:
        pass
    return hosts


def _check_messages(messages, char_limit):
    if not isinstance(messages, list) or not messages:
        raise ModelError('消息为空')
    for m in messages:
        if not isinstance(m, dict) or set(m) != {'role', 'content'} or \
                m['role'] not in ('system', 'user', 'assistant') or not isinstance(m['content'], str):
            raise ModelError('消息格式错误')
    chars = sum(len(m['content']) for m in messages)
    if chars > char_limit:
        raise ModelError('输入超过%s预算（%d/%d 字符），请缩短问题' %
                         ('本地短上下文' if char_limit == LOCAL_CHAR_LIMIT else '当前', chars, char_limit))
    return chars


def _post_json(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers=headers)
    with OPENER.open(req, timeout=timeout) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ModelError('模型响应过大')
    return json.loads(raw)



def local_endpoint():
    """按可达性选本地模型入口：优先局域网 192.168.1.244，回退 ZeroTier 172.25.25.240。"""
    import socket
    now = time.time()
    pick = _LOCAL_PICK
    if pick['url'] and now - pick['t'] < 30:
        return pick['url']
    for h in LOCAL_HOSTS:
        try:
            sk = socket.create_connection((h, 18091), timeout=1.0)
            sk.close()
            url = 'http://%s:18091/v1/chat/completions' % h
            pick.update(url=url, t=now)
            return url
        except Exception:
            continue
    pick.update(url=None, t=now)
    return 'http://%s:18091/v1/chat/completions' % LOCAL_HOSTS[0]


def _post_local(body, timeout):
    errs = []
    seen = set()
    for url in [local_endpoint()] + ['http://%s:18091/v1/chat/completions' % h for h in LOCAL_HOSTS]:
        if url in seen:
            continue
        seen.add(url)
        try:
            return _post_json(url, body, {'Content-Type': 'application/json'}, timeout)
        except urllib.error.URLError as exc:
            errs.append(exc)
            _LOCAL_PICK['url'] = None
    if errs:
        raise errs[-1]
    raise ModelError('本地模型入口不可用')

def _local_chat(messages, max_tokens, timeout):
    _check_messages(messages, LOCAL_CHAR_LIMIT)
    body = {'model': LOCAL_MODEL, 'messages': messages, 'stream': False,
            'max_tokens': min(int(max_tokens or 128), LOCAL_MAX_TOKENS)}
    deadline = time.monotonic() + timeout
    try:
        for attempt in range(16):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError('本地模型等待超时')
            try:
                data = _post_local(body, remaining)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 15 or deadline - time.monotonic() < 2:
                    raise ModelError('本地模型HTTP错误：%s' % exc.code) from exc
                time.sleep(2)
        else:
            raise ModelError('本地模型繁忙')
    except ModelError:
        raise
    except urllib.error.URLError as exc:
        import errno
        import socket
        reason = exc.reason
        code = getattr(reason, 'errno', None)
        if code == errno.ECONNREFUSED:
            detail = '连接被拒绝，请检查笔记本 18091 代理与 SSH 隧道'
        elif isinstance(reason, (TimeoutError, socket.timeout)) or code == errno.ETIMEDOUT:
            detail = '连接超时，请检查局域网模型链路'
        elif code in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            detail = '网络不可达，请检查有线连接'
        else:
            detail = '连接失败（%s）' % type(reason).__name__
        raise ModelError('本地模型不可用：' + detail) from exc
    except ValueError as exc:
        raise ModelError('本地模型返回非 JSON') from exc
    if data.get('model') != LOCAL_MODEL:
        raise ModelError('模型标识不一致（%s）' % str(data.get('model'))[:40])
    try:
        text = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError('本地模型响应结构异常') from exc
    if not isinstance(text, str) or not text.strip():
        raise ModelError('本地模型没有返回文本')
    return text.strip()


def _online_chat(messages, max_tokens, timeout):
    """在线模型对话。空文本 / 瞬时错误重试一次（2026-11-05：100 轮复测出现 1 次
    「在线模型没有返回文本」，网关偶发空响应；单次失败会把看板第 1 项打成 fail）。"""
    _check_messages(messages, ONLINE_CHAR_LIMIT)
    key = _env_key()
    if not key:
        raise ModelError('在线模型不可用：设备未配置 %s' % ONLINE_KEY_NAME)
    body = {'model': ONLINE_MODEL, 'messages': messages, 'stream': False,
            'max_tokens': min(int(max_tokens or 400), ONLINE_MAX_TOKENS)}
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}
    last = ModelError('在线模型没有返回文本')
    for attempt in range(2):
        retryable = False
        try:
            data = _post_json(ONLINE_ENDPOINT, body, headers, timeout)
        except urllib.error.HTTPError as exc:
            last = ModelError('在线模型HTTP错误：%s' % exc.code)
            retryable = exc.code in (429, 500, 502, 503, 504)
            data = None
        except urllib.error.URLError as exc:
            last = ModelError('在线模型不可用：%s' % type(getattr(exc, 'reason', exc)).__name__)
            retryable = True
            data = None
        except ValueError as exc:
            last = ModelError('在线模型返回非 JSON')
            retryable = True
            data = None
        if data is not None:
            try:
                text = data['choices'][0]['message'].get('content') or ''
            except (KeyError, IndexError, TypeError):
                last = ModelError('在线模型响应结构异常')
                retryable = True
                text = ''
            if text.strip():
                return text.strip(), str(data.get('model') or ONLINE_MODEL)
            else:
                last = ModelError('在线模型没有返回文本')
        if attempt == 0:
            time.sleep(1.2)
            continue
        raise last
    raise last


import collections

LAST_STATS = {'ts': 0, 'elapsed_ms': None, 'model': None, 'chars': 0, 'ok': False}
# 近 60s 的推理调用记录（时间戳, 耗时ms）——用于计算 NPU 占空比（实测口径，非硬件计数器）
CALLS = collections.deque(maxlen=512)
CALL_LOG = os.environ.get("LLM_CALL_LOG", "/data/ai_cpe/logs/llm_calls.jsonl")


def chat_meta(messages, max_tokens=None, timeout=None):
    mode = get_mode()
    t0 = time.monotonic()
    try:
        if mode == 'mt200-local':
            text = _local_chat(messages, max_tokens, timeout or 180)
            model = LOCAL_MODEL
        else:
            text, model = _online_chat(messages, max_tokens, timeout or 60)
    except Exception:
        LAST_STATS.update({'ts': time.time(), 'ok': False})
        raise
    elapsed = round((time.monotonic() - t0) * 1000)
    # 只记录可实测的量（端到端耗时 / 输出字符数），不编造 NPU 硬件利用率。
    LAST_STATS.update({'ts': time.time(), 'elapsed_ms': elapsed, 'model': model,
                       'chars': len(text or ''), 'ok': True})
    CALLS.append((time.time(), elapsed))
    try:
        os.makedirs(os.path.dirname(CALL_LOG), exist_ok=True)
        with open(CALL_LOG, "a", encoding="utf-8") as _f:
            _f.write(json.dumps({"t": time.time(), "ms": elapsed, "model": model}) + "\n")
        if os.path.getsize(CALL_LOG) > 2 * 1024 * 1024:
            os.replace(CALL_LOG, CALL_LOG + ".1")
    except Exception:
        pass
    return text, {'backend': mode, 'label': LABELS[mode], 'model': model,
                  'elapsed_ms': elapsed}


def stats():
    """最近一次推理 + 近 60s 占空比（可实测口径）。"""
    d = dict(LAST_STATS)
    now = time.time()
    recent = []
    # 跨进程汇总：语音助手与看板各自调用 LLM，占空比必须合并统计
    try:
        with open(CALL_LOG, encoding="utf-8") as f:
            for ln in f.readlines()[-300:]:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if now - float(o.get("t", 0)) <= 60:
                    recent.append((float(o["t"]), float(o.get("ms", 0))))
    except Exception:
        recent = []
    if not recent:
        recent = [(t, e) for t, e in list(CALLS) if now - t <= 60]
    busy_ms = sum(e for _, e in recent)
    d['calls_60s'] = len(recent)
    d['busy_ms_60s'] = busy_ms
    # 占空比：近 60s 内推理实际占用时长 / 60s（估算口径，不是硬件计数器）
    d['util_60s'] = round(min(100.0, busy_ms / 600.0), 1)
    d['idle'] = not recent
    d['last_ts'] = max((t for t, _ in recent), default=0)
    d['mode'] = get_mode()
    return d


def chat(messages, max_tokens=None, timeout=None):
    return chat_meta(messages, max_tokens=max_tokens, timeout=timeout)[0]


SYSTEM_PROMPT = ('你是部署在%s的助手。只用简体中文短答，不声称访问云端，不编造实时数据。')


def ask_meta(text, timeout=None):
    mode = get_mode()
    sysmsg = SYSTEM_PROMPT % ('MT200' if mode == 'mt200-local' else 'RG660MK（在线模型）')
    return chat_meta([{'role': 'system', 'content': sysmsg},
                      {'role': 'user', 'content': text}], max_tokens=200, timeout=timeout)


def ask(text, timeout=None):
    return ask_meta(text, timeout=timeout)[0]


def describe():
    """给看板/接口展示的当前通道信息；不含任何凭据。"""
    try:
        mode = get_mode()
        err = ''
    except ModelError as exc:
        mode, err = 'hermes', str(exc)
    info = {
        'mode': mode,
        'label': LABELS.get(mode, mode),
        'backend': 'MT200 本地推理' if mode == 'mt200-local' else '在线 DeepSeek',
        'model': LOCAL_MODEL if mode == 'mt200-local' else ONLINE_MODEL,
        'endpoint': local_endpoint() if mode == 'mt200-local' else ONLINE_ENDPOINT,
        'dsh_console': 'http://127.0.0.1:3081/',
        'error': err,
    }
    if mode == 'hermes':
        info['key_present'] = bool(_env_key())
    return info
