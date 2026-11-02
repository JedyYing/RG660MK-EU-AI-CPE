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
                data = _post_json(LOCAL_ENDPOINT, body, {'Content-Type': 'application/json'},
                                  remaining)
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
    _check_messages(messages, ONLINE_CHAR_LIMIT)
    key = _env_key()
    if not key:
        raise ModelError('在线模型不可用：设备未配置 %s' % ONLINE_KEY_NAME)
    body = {'model': ONLINE_MODEL, 'messages': messages, 'stream': False,
            'max_tokens': min(int(max_tokens or 400), ONLINE_MAX_TOKENS)}
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}
    try:
        data = _post_json(ONLINE_ENDPOINT, body, headers, timeout)
    except urllib.error.HTTPError as exc:
        raise ModelError('在线模型HTTP错误：%s' % exc.code) from exc
    except urllib.error.URLError as exc:
        raise ModelError('在线模型不可用：%s' % type(getattr(exc, 'reason', exc)).__name__) from exc
    except ValueError as exc:
        raise ModelError('在线模型返回非 JSON') from exc
    try:
        text = data['choices'][0]['message'].get('content') or ''
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError('在线模型响应结构异常') from exc
    if not text.strip():
        raise ModelError('在线模型没有返回文本')
    return text.strip(), str(data.get('model') or ONLINE_MODEL)


LAST_STATS = {'ts': 0, 'elapsed_ms': None, 'model': None, 'chars': 0, 'ok': False}


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
    return text, {'backend': mode, 'label': LABELS[mode], 'model': model,
                  'elapsed_ms': elapsed}


def stats():
    """最近一次推理的可实测指标（供看板展示，不含硬件占用率）。"""
    d = dict(LAST_STATS)
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
        'endpoint': LOCAL_ENDPOINT if mode == 'mt200-local' else ONLINE_ENDPOINT,
        'dsh_console': 'http://127.0.0.1:3081/',
        'error': err,
    }
    if mode == 'hermes':
        info['key_present'] = bool(_env_key())
    return info
