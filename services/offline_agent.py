"""有限轮本地Agent：模型只能选择白名单，执行端不接受shell、路径或URL。"""
import json
import re
import sys
import threading
from llm_gateway import chat, ask, ModelError as LocalModelError

TOOLS = {'vision', 'photo_search', 'video_diag', 'home_status', 'home_control', 'net_status', 'nas_status', 'casaos_status', 'answer'}
PROMPT = '你是工具分类器。只返回一个JSON。例：找云海照片=>{"tool":"photo_search","args":{"query":"云海"}}；开灯=>{"tool":"home_control","args":{"action":"on"}}；关灯=>{"tool":"home_control","args":{"action":"off"}}；灯状态=>{"tool":"home_status","args":{}}。其余tool：vision视觉、video_diag视频排障、net_status选网、nas_status存储、casaos_status管理、answer问答；args均为{}。args必须是对象，禁止新增字段。'
_EXEC_LOCK = threading.Lock()

# 确定性路由：固定说法直接映射工具，不依赖本地小模型生成 JSON（更快也更稳）。
_ROUTES = [
    (r'(开灯|打开灯|把灯打开|点亮灯)', {'tool': 'home_control', 'args': {'action': 'on'}}),
    (r'(关灯|关闭灯|把灯关掉|关掉灯|熄灭灯)', {'tool': 'home_control', 'args': {'action': 'off'}}),
    (r'(灯.{0,8}(状态|亮|灭|开着|关着)|(状态|看看|查).{0,6}灯)', {'tool': 'home_status', 'args': {}}),
    (r'(视频|卡顿|帧率|拉流)', {'tool': 'video_diag', 'args': {}}),
    (r'(选网|网络质量|邻区|信号质量|RSRP)', {'tool': 'net_status', 'args': {}}),
    (r'(存储|NAS|相册|照片库|磁盘|硬盘)', {'tool': 'nas_status', 'args': {}}),
    (r'(CasaOS|应用商店|管理中心)', {'tool': 'casaos_status', 'args': {}}),
    (r'(视觉|看一眼|画面|摄像头|检测.{0,4}(人|目标)|姿态|人脸)', {'tool': 'vision', 'args': {}}),
]

def validate_plan(raw):
    raw = raw.strip()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        blocks = re.findall(r'```json\s*(.*?)```', raw, re.S | re.I)
        # 唯一JSON代码块可容忍外部解释；拒绝多个计划或其他代码块。
        if len(blocks) != 1 or len(re.findall(r'```', raw)) != 2:
            raise ValueError('模型未返回唯一JSON计划')
        plan = json.loads(blocks[0])
    if not isinstance(plan, dict) or set(plan) != {'tool', 'args'} or plan['tool'] not in TOOLS or not isinstance(plan['args'], dict):
        raise ValueError('模型工具计划格式无效，未执行任务')
    tool, args = plan['tool'], plan['args']
    if tool == 'photo_search':
        if set(args) != {'query'} or not isinstance(args['query'], str) or not 1 <= len(args['query']) <= 120:
            raise ValueError('invalid query')
    elif tool == 'home_control':
        if set(args) != {'action'} or args['action'] not in ('on', 'off'):
            raise ValueError('invalid action')
    elif args:
        raise ValueError('unexpected parameters')
    return plan

def make_plan(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 180:
        raise ValueError('输入需为1～180字')
    # 明确检索句式使用确定性路由，不让小模型把检索误分成视觉控制。
    clean = text.strip()
    matched = re.fullmatch(r'(?:请帮我|帮我|请)?(?:检索|搜索|查找|找一下|找出|找)\s*[:：]?\s*(.+)', clean)
    if matched:
        query = matched.group(1).strip().rstrip('。！？!?')
        if re.search(r'开灯|关灯|打开|关闭|删除|上传|发送|执行|运行|脚本|命令|忽略|然后|同时|并且|[;；\n]', query):
            raise ValueError('检索请求包含其他动作，请分开提交；未执行任何控制')
        query = re.sub(r'^(?:图片|照片)\s*[:：]\s*', '', query)
        query = re.sub(r'(?:的)?(?:照片|图片|相片|图像)$', '', query).strip()
        return validate_plan(json.dumps({'tool': 'photo_search', 'args': {'query': query}}, ensure_ascii=False))
    # 2026-11-02：固定指令走确定性路由。本地 7B 模型生成一个 40 token 的 JSON 要 20s+，
    # 且经常格式不稳；把高频控制/查询句子从模型手里拿回来，只让模型处理自由问答。
    for pattern, plan in _ROUTES:
        if re.search(pattern, clean):
            return validate_plan(json.dumps(plan, ensure_ascii=False))
    # 未命中固定指令：直接走自由问答，不再让小模型做工具分类
    # （本地 7B 生成 JSON 又慢又不稳；分类失败会把正常提问判成失败）。
    return validate_plan(json.dumps({'tool': 'answer', 'args': {}}, ensure_ascii=False))

def board_module():
    sys.path.insert(0, '/data/ai_cpe/hermes/home/aicpe_demo')
    import aicpe_demo
    return aicpe_demo

def execute_readonly(plan):
    tool = plan['tool']
    if tool == 'home_control':
        return {'status': 'confirmation_required', 'action': plan['args']['action'], 'detail': '灯控需要页面明确确认，语音不能直接执行'}
    if tool == 'photo_search':
        return board_module().search_local_photos(plan['args']['query'])
    board = board_module()
    if tool == 'home_status':
        rc, status = board._smarthome('status')
        ok = rc == 0 and status.get('success')
        fields = {r.get('code'): r.get('value') for r in status.get('result', [])}
        if fields.get('driver') == 'board-led':
            try:
                from pathlib import Path
                actual = int(Path('/sys/class/leds/5g_evb_voice/brightness').read_text().strip())
                status['kernel_brightness'] = actual
                ok = ok and fields.get('switch_led') is bool(actual)
            except (OSError, ValueError):
                ok = False
        return {'status': 'pass' if ok else 'fail', 'result': status}
    funcs = {'vision': board.check_vision, 'video_diag': board.check_diag, 'net_status': board.check_ai_net, 'nas_status': board.check_nas, 'casaos_status': board.check_casaos_center}
    it = {'idx': 0, 'name': tool, 'status': 'pending', 'metrics': [], 'detail': '', 't0': None, 'dur': None}
    fn = funcs[tool]
    if tool == 'video_diag':
        fn(it)
    else:
        fn(it, no_audio=True)
    return {'status': it['status'], 'detail': it['detail'], 'metrics': it['metrics']}

def device_clock_reply(text):
    query = re.sub(r'[\s，。！？、,.!?]', '', text)
    if re.fullmatch(r'(?:请问|告诉我|查看|查询)?(?:现在|当前)?(?:几点(?:钟)?了?|什么时间|日期|今天(?:是)?几号|今天星期几)', query):
        import time
        return time.strftime('设备时间：%Y年%m月%d日 %H点%M分。') + '时钟同步尚未核验。'
    return None


def run(text):
    if not _EXEC_LOCK.acquire(blocking=False):
        return {'status': 'busy', 'reply': '本地任务正在处理，请稍后再试。'}
    try:
        if not isinstance(text, str) or not text.strip() or len(text) > 180:
            raise ValueError('输入需为1～180字')
        clock_reply = device_clock_reply(text)
        if clock_reply:
            return {'status': 'done', 'tool': 'local_clock', 'reply': clock_reply, 'clock_verified': False}
        plan = make_plan(text)
        if plan['tool'] == 'answer':
            return {'status': 'done', 'tool': 'answer', 'reply': ask(text), 'answer_verified': False}
        if plan['tool'] == 'photo_search':
            # 用户原文中的日期/对象优先，模型不能静默改写约束。
            plan['args']['query'] = text[:120]
        result = execute_readonly(plan)
        if result.get('status') == 'confirmation_required':
            return {'status': 'confirmation_required', 'tool': plan['tool'], 'args': plan['args'], 'reply': result['detail']}
        # 硬结果必须保留：回复用确定性文案，模型只做可选解释（不让小模型拖慢/改写结论）。
        explanation = ''
        if plan['tool'] == 'answer':
            explanation = result.get('reply', '')
        if plan['tool'] == 'home_status':
            st = result.get('result', {})
            fields = {r.get('code'): r.get('value') for r in st.get('result', [])}
            reply = ('仅读取状态，没有发送开关命令。当前灯为' + ('开。' if fields.get('switch_led') is True else '关。' if fields.get('switch_led') is False else '未知。')) if result.get('status') == 'pass' else '灯状态读取失败，没有执行控制。'
        elif plan['tool'] == 'photo_search':
            reply = '图片检索返回%d个候选，相关性需核对。' % result.get('count', 0)
        else:
            reply = '只读检查状态：%s。%s' % (result.get('status', 'unknown'), result.get('detail', ''))
        return {'status': result.get('status', 'done'), 'tool': plan['tool'], 'reply': reply, 'model_explanation_unverified': explanation, 'result': result}
    except (LocalModelError, ValueError, TypeError, KeyError) as exc:
        return {'status': 'fail', 'reply': '本地任务失败：' + str(exc)[:120]}
    finally:
        _EXEC_LOCK.release()
