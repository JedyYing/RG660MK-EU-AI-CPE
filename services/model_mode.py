"""AI CPE 外围模式；不读取或改写 Hermes 配置。"""
import json
import os
import tempfile

MODE_FILE = '/data/ai_cpe/local-model-mode.json'
MODES = ('hermes', 'mt200-local')

class ModeError(RuntimeError):
    pass

def get_mode(path=None):
    path = path or MODE_FILE
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return 'hermes'
    except (OSError, ValueError) as exc:
        raise ModeError('模式配置无效，停止处理，不自动调用云端') from exc
    if not isinstance(data, dict) or set(data) != {'mode'} or data['mode'] not in MODES:
        raise ModeError('模式配置无效，停止处理，不自动调用云端')
    return data['mode']

def set_mode(mode, path=None):
    if mode not in MODES:
        raise ModeError('未知模式')
    path = path or MODE_FILE
    fd, tmp = tempfile.mkstemp(prefix='.model-mode-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'mode': mode}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def bind_handler(mode, original, local):
    if mode not in MODES:
        raise ModeError('未知模式')
    # 返回固定函数；后台线程不会重新读取配置而误切通道。
    return original if mode == 'hermes' else local
