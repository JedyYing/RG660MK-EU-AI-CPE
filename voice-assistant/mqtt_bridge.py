#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK MQTT 发布脚本：灯泡控制 + MQTT Discovery

用法:
    python3 mqtt_bridge.py daemon     # 常驻：订阅命令主题 + 发布状态 + Discovery
    python3 mqtt_bridge.py status     # 单次：发布灯泡状态 + Discovery 配置

主题:
    homeassistant/light/rg660mk_bulb/config     # Discovery 配置
    rg660mk/bulb/state                           # 灯泡状态
    rg660mk/bulb/set                             # 灯泡命令 (订阅)

依赖: 纯 stdlib（无 paho-mqtt），最小 MQTT 3.1.1 客户端。
"""
import socket, struct, time, json, sys, os, subprocess, threading

BROKER = os.environ.get('MQTT_BROKER', '192.168.1.244')
PORT = int(os.environ.get('MQTT_PORT', '1883'))
CLIENT_ID = 'rg660mk-bridge'
BULB_SCRIPT = '/data/ai_cpe/bulb_control.py'
TOPIC_CONFIG = 'homeassistant/light/rg660mk_bulb/config'
TOPIC_STATE = 'rg660mk/bulb/state'
TOPIC_CMD = 'rg660mk/bulb/set'
TOPIC_AVAIL = 'rg660mk/bulb/available'

# ---- 最小 MQTT 3.1.1 客户端 ----
class MQTT:
    def __init__(self, host, port, client_id):
        self.host = host; self.port = port; self.client_id = client_id
        self.sock = None; self._msgid = 0

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((self.host, self.port))
        # MQTT 3.1.1 CONNECT: protocol name + level + flags + keepalive + client_id
        proto = struct.pack('>H', 4) + b'MQTT'  # "MQTT"
        level = struct.pack('B', 4)              # protocol level 4
        flags = struct.pack('B', 0x02)           # clean session
        keepalive = struct.pack('>H', 60)
        cid = struct.pack('>H', len(self.client_id)) + self.client_id.encode()
        payload = proto + level + flags + keepalive + cid
        pkt = self._build(0x10, payload)
        self.sock.sendall(pkt)
        resp = self.sock.recv(4)
        if resp[0] != 0x20 or resp[3] != 0:
            raise Exception('CONNECT failed: %s' % resp.hex())

    def publish(self, topic, msg, retain=False):
        self._msgid += 1
        payload = struct.pack('>H', len(topic)) + topic.encode() + msg.encode()
        qos = 0
        flags = 0x30 | (qos << 1) | (1 if retain else 0)
        pkt = struct.pack('B', flags) + self._encode_len(len(payload)) + payload
        self.sock.sendall(pkt)

    def subscribe(self, topic, qos=0):
        self._msgid += 1
        payload = struct.pack('>H', self._msgid)
        payload += struct.pack('>H', len(topic)) + topic.encode() + struct.pack('B', qos)
        pkt = self._build(0x82, payload)
        self.sock.sendall(pkt)

    def _build(self, type_byte, payload):
        return struct.pack('B', type_byte) + self._encode_len(len(payload)) + payload

    def _encode_len(self, length):
        result = b''
        while True:
            b = length % 128
            length //= 128
            if length > 0:
                b |= 0x80
            result += struct.pack('B', b)
            if length == 0:
                break
        return result

    def close(self):
        if self.sock:
            try: self.sock.close()
            except: pass


# ---- 灯泡控制 ----
def bulb_command(action):
    """action: on/off/toggle/status"""
    r = subprocess.run(['python3', BULB_SCRIPT, action], capture_output=True, text=True, timeout=15)
    return (r.stdout or r.stderr).strip()


def get_bulb_status():
    """查询灯泡当前状态"""
    try:
        r = subprocess.run(['python3', BULB_SCRIPT, 'status'], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        if data.get('success'):
            for dp in data.get('result', []):
                if dp.get('code') == 'switch_led':
                    return 'ON' if dp.get('value') else 'OFF'
            # 有些灯泡 status 不含 switch_led，用 work_mode 推断
            for dp in data.get('result', []):
                if dp.get('code') == 'work_mode':
                    return 'ON'  # 有工作模式说明灯亮着
    except:
        pass
    return 'unknown'


def send_discovery(mqtt):
    """发送 MQTT Discovery 配置，让 HA 自动发现灯泡"""
    config = {
        "name": "RG660MK Bulb",
        "unique_id": "rg660mk_bulb_001",
        "command_topic": TOPIC_CMD,
        "state_topic": TOPIC_STATE,
        "availability_topic": TOPIC_AVAIL,
        "payload_available": "online",
        "payload_not_available": "offline",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {
            "identifiers": ["rg660mk"],
            "name": "RG660MK AI CPE",
            "model": "RG660MK-EU",
            "manufacturer": "Quectel"
        }
    }
    mqtt.publish(TOPIC_CONFIG, json.dumps(config), retain=True)
    mqtt.publish(TOPIC_AVAIL, 'online', retain=True)
    print('[MQTT] Discovery sent: %s' % TOPIC_CONFIG)


def send_status(mqtt):
    """发布灯泡当前状态"""
    state = get_bulb_status()
    mqtt.publish(TOPIC_STATE, state, retain=True)
    print('[MQTT] State: %s -> %s' % (TOPIC_STATE, state))


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'

    mqtt = MQTT(BROKER, PORT, CLIENT_ID)
    mqtt.connect()
    print('[MQTT] Connected to %s:%d' % (BROKER, PORT))

    if action == 'status':
        send_discovery(mqtt)
        send_status(mqtt)
        mqtt.close()
        return 0

    if action == 'daemon':
        send_discovery(mqtt)
        send_status(mqtt)
        mqtt.subscribe(TOPIC_CMD)
        print('[MQTT] Subscribed to %s' % TOPIC_CMD)
        print('[MQTT] Daemon running...')

        # 简单接收（非阻塞轮询）
        mqtt.sock.settimeout(30)
        while True:
            try:
                data = mqtt.sock.recv(4096)
                if not data:
                    break
                # 解析 PUBLISH (0x30)
                if data[0] & 0xF0 == 0x30:
                    # 简单提取 payload
                    topic_len = struct.unpack('>H', data[2:4])[0]
                    topic = data[4:4+topic_len].decode()
                    payload = data[4+topic_len:].decode('utf-8', errors='ignore')
                    print('[MQTT] Received: %s = %s' % (topic, payload))
                    if topic == TOPIC_CMD:
                        action = 'on' if payload == 'ON' else 'off'
                        result = bulb_command(action)
                        print('[BULB] %s -> %s' % (action, result))
                        # 发布新状态
                        state = 'ON' if action == 'on' else 'OFF'
                        mqtt.publish(TOPIC_STATE, state, retain=True)
            except socket.timeout:
                # 心跳：重新发布状态
                send_status(mqtt)
            except Exception as e:
                print('[MQTT] Error: %s' % e)
                time.sleep(5)
                mqtt.connect()
                send_discovery(mqtt)
                mqtt.subscribe(TOPIC_CMD)

    mqtt.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
