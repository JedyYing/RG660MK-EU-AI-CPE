#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tier-1 TCP 层分析器: 手写 pcap 解析 (零依赖), 提取 重传/RTT/零窗口/拥塞骤降/SNI/分片节奏"""
import sys, json, struct
from collections import defaultdict

PCAP = sys.argv[1]
CLIENT_IP = sys.argv[2] if len(sys.argv) > 2 else '192.168.1.217'
OUTJSON = sys.argv[3] if len(sys.argv) > 3 else '/tmp/t1_tcp.json'

def parse_eth(f):
    eth = f.read(14)
    if len(eth) < 14:
        return None
    etype = struct.unpack('!H', eth[12:14])[0]
    return etype

def parse_ipv4(b):
    if len(b) < 20:
        return None
    vihl = b[0]
    if (vihl >> 4) != 4:
        return None
    ihl = (vihl & 0xF) * 4
    tot = struct.unpack('!H', b[2:4])[0]
    proto = b[9]
    src = '.'.join(str(x) for x in b[12:16])
    dst = '.'.join(str(x) for x in b[16:20])
    return {'ihl': ihl, 'tot': tot, 'proto': proto, 'src': src, 'dst': dst}

def parse_tcp(b, src, dst):
    if len(b) < 20:
        return None
    sport, dport = struct.unpack('!HH', b[0:4])
    seq, ack = struct.unpack('!II', b[4:12])
    doff = (b[12] >> 4) * 4
    flags = b[13]
    window = struct.unpack('!H', b[14:16])[0]
    payload_off = doff
    tsval = tsecr = None
    if doff > 20 and len(b) >= doff:
        opts = b[20:doff]
        i = 0
        while i < len(opts):
            k = opts[i]
            if k == 0:
                break
            if k == 1:
                i += 1
                continue
            if i + 1 >= len(opts):
                break
            ln = opts[i + 1]
            if ln < 2 or i + ln > len(opts):
                break
            if k == 8 and ln == 10:
                tsval, tsecr = struct.unpack('!II', opts[i + 2:i + 10])
            i += ln
    payload_len = len(b) - payload_off if len(b) >= payload_off else 0
    return {'sport': sport, 'dport': dport, 'seq': seq, 'ack': ack,
            'flags': flags, 'window': window, 'payload_len': payload_len,
            'tsval': tsval, 'tsecr': tsecr,
            'payload': b[payload_off:payload_off + min(payload_len, 600)] if payload_len else b''}

t0 = None
last_ts = None
down_seq = defaultdict(set)
retx = 0; retx_bytes = 0; down_total = 0
zero_win = 0
rtt_samples = []
ts_map = {}
bins = defaultdict(float)
sni_list = []
flows = set()

with open(PCAP, 'rb') as f:
    gh = f.read(24)
    if len(gh) < 24:
        print("pcap 太短"); sys.exit(1)
    magic = struct.unpack('<I', gh[0:4])[0]
    if magic == 0xa1b2c3d4:
        endian = '<'
    elif magic == 0xd4c3b2a1:
        endian = '>'
    else:
        print("未知 pcap magic"); sys.exit(1)
    linktype = struct.unpack(endian + 'I', gh[20:24])[0]
    n = 0
    while True:
        ph = f.read(16)
        if len(ph) < 16:
            break
        ts_sec, ts_usec, incl, orig = struct.unpack(endian + 'IIII', ph)
        data = f.read(incl)
        if len(data) < incl:
            break
        ts = ts_sec + ts_usec / 1e6
        last_ts = ts
        if t0 is None:
            t0 = ts
        if linktype == 1:      # Ethernet
            if len(data) < 14:
                continue
            etype = struct.unpack('!H', data[12:14])[0]
            if etype == 0x8100:  # VLAN
                if len(data) < 18:
                    continue
                etype = struct.unpack('!H', data[16:18])[0]
                data = data[:12] + data[16:]
            if etype == 0x0800:
                l3 = data[14:]
            elif etype == 0x86dd:
                l3 = data[14:]
            else:
                continue
        elif linktype == 113:  # Linux cooked
            l3 = data[16:]
        else:
            continue
        if l3 and (l3[0] >> 4) == 6 and len(l3) >= 40:
            # IPv6: 40B 固定头 + 扩展头链
            if len(l3) < 40:
                continue
            nxt = l3[6]
            off = 40
            while nxt in (0, 43, 60):
                if off + 2 > len(l3):
                    nxt = -1
                    break
                nxt = l3[off]
                off += 8 + l3[off + 1] * 8
            if nxt != 6:
                continue
            src6 = ':'.join(f'{int.from_bytes(l3[i:i+2], "big"):x}' for i in (8, 10, 12, 14, 16, 18, 20, 22))
            dst6 = ':'.join(f'{int.from_bytes(l3[i:i+2], "big"):x}' for i in (24, 26, 28, 30, 32, 34, 36, 38))
            ip = {'ihl': off, 'proto': 6, 'src': src6, 'dst': dst6}
            tcp = parse_tcp(l3[off:], ip['src'], ip['dst'])
        else:
            ip = parse_ipv4(l3)
            if not ip or ip['proto'] != 6:
                continue
            tcp = parse_tcp(l3[ip['ihl']:], ip['src'], ip['dst'])
        if not tcp:
            continue
        n += 1
        src_is_client = ip['src'] == CLIENT_IP
        dst_is_client = ip['dst'] == CLIENT_IP
        if not (src_is_client or dst_is_client):
            continue
        ep1 = (ip['src'], tcp['sport'])
        ep2 = (ip['dst'], tcp['dport'])
        fid = (ep1, ep2) if ep1 < ep2 else (ep2, ep1)
        flows.add(fid)
        plen = tcp['payload_len']
        bins[int((ts - t0))] += plen * 8 / 1e6  # Mbit per 1s bin

        if dst_is_client:  # 下行
            if plen > 0:
                down_total += plen
                if tcp['seq'] in down_seq[fid]:
                    retx += 1
                    retx_bytes += plen
                else:
                    down_seq[fid].add(tcp['seq'])
                if tcp['tsval'] is not None:
                    ts_map[(fid, tcp['tsval'])] = ts
        else:              # 上行
            if tcp['window'] == 0:
                zero_win += 1
            if tcp['tsecr'] is not None:
                key = (fid, tcp['tsecr'])
                if key in ts_map:
                    rtt = (ts - ts_map[key]) * 1000
                    if 0 < rtt < 5000:
                        rtt_samples.append(rtt)
                    del ts_map[key]
            # TLS SNI 提取 (ClientHello)
            pl = tcp['payload']
            if pl and pl[0] == 0x16 and len(pl) > 5:
                import re
                ms = re.findall(rb'[a-z0-9\-]{3,}(?:\.[a-z0-9\-]{2,}){1,}', pl[:400])
                seen = set()
                for m in ms:
                    s = m.decode(errors='ignore')
                    if s not in seen and '\x00' not in s:
                        seen.add(s)
                        sni_list.append(s)

rtt_stat = {}
if rtt_samples:
    avg = sum(rtt_samples) / len(rtt_samples)
    var = sum((x - avg) ** 2 for x in rtt_samples) / len(rtt_samples)
    jit = sum(abs(rtt_samples[i] - rtt_samples[i-1]) for i in range(1, len(rtt_samples))) / max(len(rtt_samples) - 1, 1)
    rtt_stat = {'samples': len(rtt_samples), 'avg_ms': round(avg, 1),
                'std_ms': round(var ** 0.5, 1), 'jitter_ms': round(jit, 1),
                'min_ms': round(min(rtt_samples), 1), 'max_ms': round(max(rtt_samples), 1)}

maxbin = max(bins.keys()) if bins else 0
rates = [round(bins.get(i, 0), 3) for i in range(maxbin + 1)]

cwnd_drops = []
for i in range(1, len(rates) - 2):
    if rates[i] > 0.5 and rates[i] >= 2 * max(rates[i-1], 0.05):
        nxt = min(rates[i+1:i+3]) if i + 1 < len(rates) else 0
        if nxt <= rates[i] * 0.5:
            cwnd_drops.append({'t_s': i, 'before_Mbps': round(rates[i], 2), 'after_Mbps': round(nxt, 2)})

bursts = []
in_burst = False; start = 0
for i, r in enumerate(rates):
    if r >= 0.3 and not in_burst:
        in_burst = True; start = i
    elif r < 0.05 and in_burst:
        in_burst = False
        bursts.append({'start_s': start, 'dur_s': i - start, 'size_Mbit': round(sum(rates[start:i]), 2)})
if in_burst:
    bursts.append({'start_s': start, 'dur_s': len(rates) - start, 'size_Mbit': round(sum(rates[start:]), 2)})

result = {
    'client_ip': CLIENT_IP,
    'duration_s': round((last_ts - t0) if last_ts and t0 else 0, 1),
    'tcp_packets_parsed': n,
    'flows': len(flows),
    'downlink_total_MB': round(down_total / 1024 / 1024, 2),
    'tcp_retransmissions': retx,
    'tcp_retx_bytes': retx_bytes,
    'ip_loss_estimate_pct': round(retx_bytes / down_total * 100, 2) if down_total else 0,
    'zero_window_events': zero_win,
    'rtt': rtt_stat,
    'congestion_drop_events': cwnd_drops,
    'downlink_rate_series_Mbps': rates,
    'chunk_bursts': bursts,
    'tls_sni_domains': sorted(set(sni_list))[:20],
}
with open(OUTJSON, 'w') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
print("===== Tier-1 TCP 层分析 =====")
print(json.dumps({k: v for k, v in result.items() if k != 'downlink_rate_series_Mbps'},
                 indent=2, ensure_ascii=False))
