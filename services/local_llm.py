"""固定物理局域网端点；不读取云端凭据、不使用代理、不跟随重定向。"""
import json
import urllib.request
import urllib.error

ENDPOINT = 'http://192.168.1.244:18091/v1/chat/completions'
MODEL = 'deepseek-7b-npu'

class LocalModelError(RuntimeError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalModelError('禁止重定向')

class FixedLAN(urllib.request.BaseHandler):
    def http_request(self, req):
        from urllib.parse import urlsplit
        u = urlsplit(req.full_url)
        if u.scheme != 'http' or u.username or u.password or u.hostname not in ('127.0.0.1', '192.168.1.244'):
            raise LocalModelError('拒绝非固定物理局域网目标')
        return req
    https_request = http_request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), FixedLAN())

def chat(messages, timeout=180):
    if not isinstance(messages, list) or not messages:
        raise LocalModelError('消息为空')
    for m in messages:
        if set(m) != {'role', 'content'} or m['role'] not in ('system', 'user', 'assistant') or not isinstance(m['content'], str):
            raise LocalModelError('消息格式错误')
    # 当前原桥接只有估算器，且不实施max_tokens：以短输入保守限额，不谎称精确token预算。
    chars = sum(len(m['content']) for m in messages)
    if chars > 600:
        raise LocalModelError('输入超过当前短上下文预算，请缩短问题')
    body = json.dumps({'model': MODEL, 'messages': messages, 'stream': False, 'max_tokens': 128}, ensure_ascii=False).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={'Content-Type': 'application/json'})
    try:
        import time
        deadline = time.monotonic() + timeout
        for attempt in range(16):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalModelError('本地模型等待超时')
                with OPENER.open(req, timeout=remaining) as response:
                    raw = response.read(65537)
                break
            except urllib.error.HTTPError as exc:
                # 只重试网关明确拒绝且未执行的429，不重试超时或未知执行状态。
                if exc.code != 429 or attempt == 15 or deadline - time.monotonic() < 2:
                    raise LocalModelError('本地模型HTTP错误：%s' % exc.code) from exc
                time.sleep(2)
        else:
            raise LocalModelError('本地模型繁忙')
        if len(raw) > 65536:
            raise LocalModelError('模型响应过大')
        data = json.loads(raw)
        if data.get('model') != MODEL:
            raise LocalModelError('模型标识不一致')
        text = data['choices'][0]['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise LocalModelError('本地模型没有返回文本')
        return text.strip()
    except LocalModelError:
        raise
    except urllib.error.URLError as exc:
        import errno
        import socket
        reason = exc.reason
        code = getattr(reason, 'errno', None)
        if code == errno.ECONNREFUSED:
            detail = '连接被拒绝，请检查局域网模型网关和SSH隧道'
        elif isinstance(reason, (TimeoutError, socket.timeout)) or code == errno.ETIMEDOUT:
            detail = '连接超时，请检查局域网模型链路'
        elif code in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            detail = '网络不可达，请检查有线连接'
        else:
            detail = '连接失败（%s）' % type(reason).__name__
        raise LocalModelError('本地模型不可用：' + detail) from exc
    except Exception as exc:
        raise LocalModelError('本地模型不可用：' + type(exc).__name__) from exc

def ask(text):
    return chat([{'role': 'system', 'content': '你是部署在MT200上的本地助手。只用简体中文短答，不声称访问云端，不编造实时数据。'}, {'role': 'user', 'content': text}])
