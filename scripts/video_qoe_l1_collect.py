#!/usr/bin/env python3
"""Collect lightweight, terminal-scoped video network evidence from RG660MK-EU.

The collector runs on the Ubuntu host and reaches the CPE through ADB. It does
not write to the CPE, alter firewall/offload settings, or install packages.
Packet capture is limited to one associated station and 128-byte snapshots.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import signal
import statistics
import struct
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


class CollectionError(RuntimeError):
    pass


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def value_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "min": None,
            "max": None,
            "p5": None,
            "p50": None,
            "p95": None,
            "stddev": None,
            "cv": None,
        }
    mean = statistics.fmean(values)
    stddev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "mean": mean,
        "min": min(values),
        "max": max(values),
        "p5": percentile(values, 5),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "stddev": stddev,
        "cv": stddev / mean if mean > 0 else None,
    }


def mean_successive_delta(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.fmean(abs(right - left) for left, right in zip(values, values[1:]))


def fmt_num(value: Any, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "不可用"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return f"{float(value):.{digits}f}{suffix}"


def mask_mac(mac: str) -> str:
    parts = mac.split(":")
    return ":".join(parts[:2] + ["**", "**"] + parts[-2:]) if len(parts) == 6 else "***"


def mask_ip(address: str | None) -> str:
    if not address:
        return "未分配"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "***"
    if parsed.version == 4:
        octets = address.split(".")
        return ".".join(octets[:3] + ["x"])
    return f"{':'.join(parsed.exploded.split(':')[:4])}::/64"


class AdbClient:
    def __init__(self, executable: str, serial_number: str | None) -> None:
        self.prefix = [executable]
        if serial_number:
            self.prefix += ["-s", serial_number]

    def run(self, arguments: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self.prefix + arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result.stdout = result.stdout.replace("\r", "")
        result.stderr = result.stderr.replace("\r", "")
        if check and result.returncode != 0:
            raise CollectionError(
                f"ADB command failed ({result.returncode}): {' '.join(arguments)}\n{result.stderr.strip()}"
            )
        return result

    def shell(self, command: str, *, timeout: int = 30, check: bool = True) -> str:
        return self.run(["shell", command], timeout=timeout, check=check).stdout

    def popen(
        self,
        arguments: list[str],
        *,
        stdout: Any,
        stderr: Any,
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(self.prefix + arguments, stdout=stdout, stderr=stderr)


def validate_inputs(args: argparse.Namespace) -> None:
    if not 5 <= args.duration <= 1800:
        raise CollectionError("--duration must be between 5 and 1800 seconds")
    if not 1 <= args.interval <= 10:
        raise CollectionError("--interval must be between 1 and 10 seconds")
    if args.target_mac and not re.fullmatch(r"(?i)[0-9a-f]{2}(?::[0-9a-f]{2}){5}", args.target_mac):
        raise CollectionError("--target-mac is invalid")
    for field, value in (
        ("--wifi-if", args.wifi_if),
        ("--wan-if", args.wan_if),
        ("--ping-target", args.ping_target),
    ):
        if value and not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise CollectionError(f"{field} contains unsupported characters")
    if args.target_ip:
        try:
            ipaddress.ip_address(args.target_ip)
        except ValueError as exc:
            raise CollectionError("--target-ip is invalid") from exc


def parse_hostapd_clients(adb: AdbClient) -> list[dict[str, Any]]:
    interfaces_text = adb.shell(
        "iw dev 2>/dev/null | awk '$1 == \"Interface\" { i=$2 } $1 == \"type\" && $2 == \"AP\" { print i }'",
        check=False,
    )
    interfaces = [line.strip() for line in interfaces_text.splitlines() if line.strip()]
    clients: list[dict[str, Any]] = []
    for interface in interfaces:
        output = adb.shell(f"ubus call hostapd.{shlex.quote(interface)} get_clients 2>/dev/null", check=False)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            continue
        for mac, details in payload.get("clients", {}).items():
            if details.get("assoc") and details.get("authorized"):
                clients.append({"mac": mac.lower(), "interface": interface, "details": details})
    return clients


def parse_leases(adb: AdbClient) -> dict[str, dict[str, str]]:
    leases: dict[str, dict[str, str]] = {}
    for line in adb.shell("cat /tmp/dhcp.leases 2>/dev/null", check=False).splitlines():
        fields = line.split()
        if len(fields) >= 4:
            leases[fields[1].lower()] = {"expires": fields[0], "ip": fields[2], "hostname": fields[3]}
    return leases


def identify_target(adb: AdbClient, args: argparse.Namespace) -> dict[str, Any]:
    clients = parse_hostapd_clients(adb)
    requested_mac = args.target_mac.lower() if args.target_mac else None
    if requested_mac:
        matches = [entry for entry in clients if entry["mac"] == requested_mac]
        if not matches:
            raise CollectionError(f"target {mask_mac(requested_mac)} is not currently associated")
        target = matches[0]
    else:
        if len(clients) != 1:
            raise CollectionError(
                f"auto-discovery requires exactly one associated terminal; found {len(clients)}. "
                "Use --target-mac explicitly."
            )
        target = clients[0]
    if args.wifi_if and target["interface"] != args.wifi_if:
        raise CollectionError(
            f"target is associated on {target['interface']}, not requested interface {args.wifi_if}"
        )
    leases = parse_leases(adb)
    lease = leases.get(target["mac"], {})
    target["ip"] = args.target_ip or lease.get("ip")
    target["hostname"] = lease.get("hostname", "unknown")
    target["associated_client_count_start"] = len(clients)
    return target


def identify_wan_interface(adb: AdbClient, requested: str | None, ping_target: str) -> str:
    if requested:
        interface = requested
    else:
        route = adb.shell(
            f"ip -4 route get {shlex.quote(ping_target)} 2>/dev/null | head -n 1",
            check=False,
        )
        match = re.search(r"(?:^|\s)dev\s+([A-Za-z0-9_.:-]+)", route)
        if not match:
            route = adb.shell("ip -4 route show default 2>/dev/null | head -n 1", check=False)
            match = re.search(r"(?:^|\s)dev\s+([A-Za-z0-9_.:-]+)", route)
        if not match:
            raise CollectionError("could not detect the current IPv4 WAN interface; use --wan-if")
        interface = match.group(1)
    exists = adb.shell(
        f"[ -d /sys/class/net/{shlex.quote(interface)} ] && printf yes",
        check=False,
    ).strip()
    if exists != "yes":
        raise CollectionError(f"WAN interface does not exist: {interface}")
    return interface


def snapshot_command(wifi_interface: str, wan_interface: str) -> str:
    interfaces = [wifi_interface, "br-lan", wan_interface]
    interface_words = " ".join(shlex.quote(item) for item in interfaces)
    return f'''echo "__SNMP__"
cat /proc/net/snmp 2>/dev/null
echo "__NETSTAT__"
cat /proc/net/netstat 2>/dev/null
echo "__INTERFACES__"
for i in {interface_words}; do
    printf "IFACE %s " "$i"
    for key in rx_bytes tx_bytes rx_packets tx_packets rx_dropped tx_dropped rx_errors tx_errors; do
        file="/sys/class/net/$i/statistics/$key"
        [ -r "$file" ] && printf "%s=%s " "$key" "$(cat "$file")"
    done
    echo
done
echo "__QDISC__"
for i in {interface_words}; do
    echo "QDISC $i"
    tc -s qdisc show dev "$i" 2>&1
done
echo "__SYSTEM__"
date -Iseconds 2>/dev/null || date
cat /proc/loadavg
grep -E "MemTotal|MemAvailable|SwapTotal" /proc/meminfo
'''


def take_snapshot(adb: AdbClient, wifi_interface: str, wan_interface: str, path: Path) -> str:
    output = adb.shell(snapshot_command(wifi_interface, wan_interface), timeout=30)
    path.write_text(output, encoding="utf-8")
    return output


def parse_proc_tables(snapshot: str) -> dict[str, dict[str, int]]:
    tables: dict[str, dict[str, int]] = {}
    lines = snapshot.splitlines()
    for index in range(len(lines) - 1):
        header = lines[index].split()
        values = lines[index + 1].split()
        if not header or not values or header[0] != values[0] or not header[0].endswith(":"):
            continue
        if len(header) != len(values):
            continue
        try:
            parsed = {key: int(value) for key, value in zip(header[1:], values[1:])}
        except ValueError:
            continue
        tables[header[0][:-1]] = parsed
    return tables


def parse_interface_counters(snapshot: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in snapshot.splitlines():
        if not line.startswith("IFACE "):
            continue
        fields = line.split()
        counters: dict[str, int] = {}
        for field in fields[2:]:
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            try:
                counters[key] = int(value)
            except ValueError:
                pass
        result[fields[1]] = counters
    return result


def parse_qdisc_drops(snapshot: str) -> dict[str, int]:
    result: dict[str, int] = {}
    current: str | None = None
    for line in snapshot.splitlines():
        if line.startswith("QDISC "):
            current = line.split(maxsplit=1)[1]
            result.setdefault(current, 0)
            continue
        if current:
            for match in re.finditer(r"\bdropped\s+(\d+)", line):
                result[current] += int(match.group(1))
    return result


def nested_counter_delta(
    before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for section in sorted(set(before) | set(after)):
        values: dict[str, int] = {}
        for key in sorted(set(before.get(section, {})) | set(after.get(section, {}))):
            if key in before.get(section, {}) and key in after.get(section, {}):
                values[key] = after[section][key] - before[section][key]
        result[section] = values
    return result


def build_sampler_command(
    duration: int,
    interval: int,
    wifi_interface: str,
    wan_interface: str,
    mac: str,
) -> str:
    interface = shlex.quote(wifi_interface)
    wan = shlex.quote(wan_interface)
    station = shlex.quote(mac)
    return f'''duration={duration}
interval={interval}
interface={interface}
wan_interface={wan}
station_mac={station}
read_stat() {{ cat "/sys/class/net/$1/statistics/$2" 2>/dev/null; }}
echo "epoch,monotonic_s,sta_tx_bytes,sta_rx_bytes,sta_tx_packets,sta_rx_packets,sta_tx_retries,sta_tx_failed,signal_dbm,wifi_tx_bytes,wifi_rx_bytes,wifi_tx_packets,wifi_rx_packets,wifi_tx_dropped,wifi_rx_dropped,wifi_tx_errors,wifi_rx_errors,wan_rx_bytes,wan_tx_bytes,wan_rx_packets,wan_tx_packets,wan_rx_dropped,wan_tx_dropped,wan_rx_errors,wan_tx_errors"
start=$(cut -d. -f1 /proc/uptime)
end=$((start + duration))
while :; do
    station_data=$(iw dev "$interface" station get "$station_mac" 2>/dev/null)
    sta_tx_bytes=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*tx bytes:/ {{print $3; exit}}')
    sta_rx_bytes=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*rx bytes:/ {{print $3; exit}}')
    sta_tx_packets=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*tx packets:/ {{print $3; exit}}')
    sta_rx_packets=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*rx packets:/ {{print $3; exit}}')
    sta_tx_retries=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*tx retries:/ {{print $3; exit}}')
    sta_tx_failed=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*tx failed:/ {{print $3; exit}}')
    signal_dbm=$(printf "%s\\n" "$station_data" | awk '/^[[:space:]]*signal:/ {{print $2; exit}}')
    epoch=$(date +%s)
    monotonic=$(cut -d\  -f1 /proc/uptime)
    echo "$epoch,$monotonic,$sta_tx_bytes,$sta_rx_bytes,$sta_tx_packets,$sta_rx_packets,$sta_tx_retries,$sta_tx_failed,$signal_dbm,$(read_stat "$interface" tx_bytes),$(read_stat "$interface" rx_bytes),$(read_stat "$interface" tx_packets),$(read_stat "$interface" rx_packets),$(read_stat "$interface" tx_dropped),$(read_stat "$interface" rx_dropped),$(read_stat "$interface" tx_errors),$(read_stat "$interface" rx_errors),$(read_stat "$wan_interface" rx_bytes),$(read_stat "$wan_interface" tx_bytes),$(read_stat "$wan_interface" rx_packets),$(read_stat "$wan_interface" tx_packets),$(read_stat "$wan_interface" rx_dropped),$(read_stat "$wan_interface" tx_dropped),$(read_stat "$wan_interface" rx_errors),$(read_stat "$wan_interface" tx_errors)"
    now=$(cut -d. -f1 /proc/uptime)
    [ "$now" -ge "$end" ] && break
    sleep "$interval"
done
'''


def stop_process(process: subprocess.Popen[Any] | None, *, grace_seconds: int = 5) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def find_remote_capture_pids(adb: AdbClient, mac: str) -> list[int]:
    command = f'''ps w | awk '$5 == "tcpdump" && index($0, "{mac}") {{print $1}}' '''
    output = adb.shell(command, timeout=10, check=False)
    return [int(line) for line in output.splitlines() if line.strip().isdigit()]


def cleanup_remote_capture(adb: AdbClient, mac: str) -> None:
    for pid in find_remote_capture_pids(adb, mac):
        adb.shell(f"kill -INT {pid} 2>/dev/null", timeout=10, check=False)


def numeric(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def counter_change(first: dict[str, str], last: dict[str, str], key: str) -> int | None:
    left = numeric(first, key)
    right = numeric(last, key)
    if left is None or right is None or right < left:
        return None
    return int(right - left)


def analyze_station_samples(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.DictReader(line.replace("\r", "") for line in handle if line.strip()))
    valid = [row for row in rows if numeric(row, "monotonic_s") is not None]
    down_rates: list[float] = []
    up_rates: list[float] = []
    wan_down_rates: list[float] = []
    periods: list[dict[str, float]] = []
    for previous, current in zip(valid, valid[1:]):
        left_time = numeric(previous, "monotonic_s")
        right_time = numeric(current, "monotonic_s")
        if left_time is None or right_time is None or right_time <= left_time:
            continue
        elapsed = right_time - left_time
        values: dict[str, float] = {"elapsed": elapsed, "time": right_time}
        complete = True
        for output_name, field in (
            ("down", "wifi_tx_bytes"),
            ("up", "wifi_rx_bytes"),
            ("wan_down", "wan_rx_bytes"),
        ):
            left = numeric(previous, field)
            right = numeric(current, field)
            if left is None or right is None or right < left:
                complete = False
                break
            values[output_name] = (right - left) * 8.0 / elapsed / 1_000_000.0
        if not complete:
            continue
        down_rates.append(values["down"])
        up_rates.append(values["up"])
        wan_down_rates.append(values["wan_down"])
        periods.append(values)

    rolling_5s: list[float] = []
    for index in range(len(periods)):
        elapsed = 0.0
        weighted = 0.0
        for period in periods[index:]:
            remaining = 5.0 - elapsed
            if remaining <= 0:
                break
            used = min(period["elapsed"], remaining)
            weighted += period["down"] * used
            elapsed += used
            if elapsed >= 5.0:
                rolling_5s.append(weighted / elapsed)
                break

    signals = [numeric(row, "signal_dbm") for row in valid]
    signal_values = [value for value in signals if value is not None]
    duration = 0.0
    if len(valid) >= 2:
        duration = float(numeric(valid[-1], "monotonic_s") or 0) - float(
            numeric(valid[0], "monotonic_s") or 0
        )
    result: dict[str, Any] = {
        "sample_rows": len(valid),
        "duration_s": duration,
        "actual_interval_s": value_stats([period["elapsed"] for period in periods]),
        "downlink_mbps": value_stats(down_rates),
        "uplink_mbps": value_stats(up_rates),
        "wan_downlink_mbps": value_stats(wan_down_rates),
        "downlink_5s_mbps": value_stats(rolling_5s),
        "signal_dbm": value_stats(signal_values),
        "station_missing_samples": sum(1 for row in valid if numeric(row, "signal_dbm") is None),
    }
    if valid:
        result["counter_deltas"] = {
            key: counter_change(valid[0], valid[-1], key)
            for key in (
                "sta_tx_retries",
                "sta_tx_failed",
                "wifi_tx_packets",
                "wifi_rx_packets",
                "wifi_tx_dropped",
                "wifi_rx_dropped",
                "wifi_tx_errors",
                "wifi_rx_errors",
                "wan_rx_packets",
                "wan_tx_packets",
                "wan_rx_dropped",
                "wan_tx_dropped",
                "wan_rx_errors",
                "wan_tx_errors",
            )
        }
    else:
        result["counter_deltas"] = {}
    return result


def parse_ping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    reply_times = [float(item) for item in re.findall(r"time[=<]([0-9.]+)\s*ms", text)]
    summary = re.search(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received.*?(\d+(?:\.\d+)?)%\s+packet loss",
        text,
        re.S,
    )
    transmitted = int(summary.group(1)) if summary else None
    received = int(summary.group(2)) if summary else len(reply_times)
    loss_percent = float(summary.group(3)) if summary else None
    timing = value_stats(reply_times)
    timing["successive_jitter_ms"] = mean_successive_delta(reply_times)
    return {
        "transmitted": transmitted,
        "received": received,
        "loss_percent": loss_percent,
        "rtt_ms": timing,
        "raw_summary_found": summary is not None,
    }


def mac_bytes(mac: str) -> bytes:
    return bytes(int(part, 16) for part in mac.split(":"))


def sequence_leq(left: int, right: int) -> bool:
    return ((right - left) & 0xFFFFFFFF) < 0x80000000


def parse_tcp_options(options: bytes) -> tuple[int | None, int | None]:
    offset = 0
    while offset < len(options):
        kind = options[offset]
        if kind == 0:
            break
        if kind == 1:
            offset += 1
            continue
        if offset + 1 >= len(options):
            break
        length = options[offset + 1]
        if length < 2 or offset + length > len(options):
            break
        if kind == 8 and length == 10:
            return struct.unpack("!II", options[offset + 2 : offset + 10])
        offset += length
    return None, None


def parse_ip_packet(data: bytes, offset: int, ethertype: int) -> dict[str, Any] | None:
    if ethertype == 0x0800:
        if len(data) < offset + 20:
            return None
        version_ihl = data[offset]
        if version_ihl >> 4 != 4:
            return None
        header_length = (version_ihl & 0x0F) * 4
        if header_length < 20 or len(data) < offset + header_length:
            return None
        total_length = struct.unpack("!H", data[offset + 2 : offset + 4])[0]
        fragment = struct.unpack("!H", data[offset + 6 : offset + 8])[0]
        protocol = data[offset + 9]
        source = str(ipaddress.ip_address(data[offset + 12 : offset + 16]))
        destination = str(ipaddress.ip_address(data[offset + 16 : offset + 20]))
        return {
            "version": 4,
            "source": source,
            "destination": destination,
            "protocol": protocol,
            "l4_offset": offset + header_length,
            "l4_length": max(0, total_length - header_length),
            "fragmented": bool(fragment & 0x3FFF),
        }
    if ethertype != 0x86DD or len(data) < offset + 40 or data[offset] >> 4 != 6:
        return None
    payload_length = struct.unpack("!H", data[offset + 4 : offset + 6])[0]
    next_header = data[offset + 6]
    source = str(ipaddress.ip_address(data[offset + 8 : offset + 24]))
    destination = str(ipaddress.ip_address(data[offset + 24 : offset + 40]))
    cursor = offset + 40
    remaining = payload_length
    fragmented = False
    while next_header in {0, 43, 44, 51, 60}:
        if len(data) < cursor + 2:
            return None
        current = next_header
        next_header = data[cursor]
        if current == 44:
            extension_length = 8
            fragmented = True
        elif current == 51:
            extension_length = (data[cursor + 1] + 2) * 4
        else:
            extension_length = (data[cursor + 1] + 1) * 8
        if extension_length > remaining:
            return None
        cursor += extension_length
        remaining -= extension_length
    return {
        "version": 6,
        "source": source,
        "destination": destination,
        "protocol": next_header,
        "l4_offset": cursor,
        "l4_length": max(0, remaining),
        "fragmented": fragmented,
    }


def new_flow_state(flow_id: str, ip_version: int, target_port: int, remote_port: int) -> dict[str, Any]:
    return {
        "flow_id": flow_id,
        "ip_version": ip_version,
        "target_port": target_port,
        "remote_port": remote_port,
        "seen_segments": {},
        "pending_up": {},
        "up_tsvals": {},
        "echoed_tsecr": set(),
        "timestamp_rtt": [],
        "ack_rtt": [],
        "last_zero": {"up": False, "down": False},
        "last_target_ack": None,
        "duplicate_ack_count": 0,
        "down_outstanding": {},
    }


def analyze_pcap(path: Path, target_mac: str) -> dict[str, Any]:
    target = mac_bytes(target_mac)
    raw_packets = 0
    duplicate_tap_packets = 0
    parsed_packets = 0
    capture_start: float | None = None
    capture_end: float | None = None
    last_fingerprint: dict[tuple[str, bytes, int], float] = {}
    protocol = defaultdict(lambda: defaultdict(lambda: {"packets": 0, "bytes": 0}))
    flows: dict[tuple[Any, ...], dict[str, Any]] = {}
    flow_generations: defaultdict[tuple[Any, ...], int] = defaultdict(int)
    retransmissions: list[dict[str, Any]] = []
    handshake_retransmissions: list[dict[str, Any]] = []
    zero_windows: list[dict[str, Any]] = []
    congestion_signals: list[dict[str, Any]] = []
    tcp_data_packets = {"up": 0, "down": 0}

    with path.open("rb") as handle:
        global_header = handle.read(24)
        if len(global_header) != 24:
            raise CollectionError("pcap is empty or missing its global header")
        magic = global_header[:4]
        formats = {
            b"\xd4\xc3\xb2\xa1": ("<", 1_000_000.0),
            b"\xa1\xb2\xc3\xd4": (">", 1_000_000.0),
            b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000.0),
            b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000.0),
        }
        if magic not in formats:
            raise CollectionError("unsupported pcap format (pcapng is not expected from tcpdump -w -)")
        endian, timestamp_divisor = formats[magic]
        snapshot_length = struct.unpack(endian + "I", global_header[16:20])[0]
        link_type = struct.unpack(endian + "I", global_header[20:24])[0]
        if link_type != 1:
            raise CollectionError(f"unsupported pcap link type {link_type}; Ethernet was expected")
        if snapshot_length < 64 or snapshot_length > 65535:
            raise CollectionError(f"invalid pcap snapshot length {snapshot_length}")

        while True:
            record_header = handle.read(16)
            if not record_header:
                break
            if len(record_header) != 16:
                break
            seconds, fraction, included_length, original_length = struct.unpack(
                endian + "IIII", record_header
            )
            if included_length > snapshot_length:
                raise CollectionError(
                    f"corrupt pcap record: captured length {included_length} exceeds snaplen {snapshot_length}"
                )
            data = handle.read(included_length)
            if len(data) != included_length:
                raise CollectionError("truncated pcap record at end of capture")
            raw_packets += 1
            timestamp = seconds + fraction / timestamp_divisor
            capture_start = timestamp if capture_start is None else capture_start
            capture_end = timestamp
            if len(data) < 14:
                continue
            destination_mac = data[0:6]
            source_mac = data[6:12]
            if source_mac == target:
                direction = "up"
            elif destination_mac == target:
                direction = "down"
            else:
                continue
            ethertype = struct.unpack("!H", data[12:14])[0]
            network_offset = 14
            while ethertype in {0x8100, 0x88A8} and len(data) >= network_offset + 4:
                ethertype = struct.unpack("!H", data[network_offset + 2 : network_offset + 4])[0]
                network_offset += 4
            digest = hashlib.blake2s(data[network_offset:], digest_size=8).digest()
            fingerprint = (direction, digest, original_length)
            previous_time = last_fingerprint.get(fingerprint)
            last_fingerprint[fingerprint] = timestamp
            if previous_time is not None and timestamp - previous_time <= 0.002:
                duplicate_tap_packets += 1
                continue

            packet = parse_ip_packet(data, network_offset, ethertype)
            if packet is None:
                continue
            parsed_packets += 1
            protocol_number = packet["protocol"]
            protocol_name = "tcp" if protocol_number == 6 else "udp" if protocol_number == 17 else "other"
            protocol[direction][protocol_name]["packets"] += 1
            protocol[direction][protocol_name]["bytes"] += original_length
            l4_offset = packet["l4_offset"]
            l4_length = packet["l4_length"]
            if packet["fragmented"] or len(data) < l4_offset + 4:
                continue
            source_port, destination_port = struct.unpack("!HH", data[l4_offset : l4_offset + 4])
            if protocol_number == 17:
                if source_port == 443 or destination_port == 443:
                    protocol[direction]["udp_443_candidate"]["packets"] += 1
                    protocol[direction]["udp_443_candidate"]["bytes"] += original_length
                continue
            if protocol_number != 6 or len(data) < l4_offset + 20:
                continue

            sequence, acknowledgment = struct.unpack("!II", data[l4_offset + 4 : l4_offset + 12])
            header_length = (data[l4_offset + 12] >> 4) * 4
            flags = data[l4_offset + 13]
            receive_window = struct.unpack("!H", data[l4_offset + 14 : l4_offset + 16])[0]
            if header_length < 20 or l4_length < header_length:
                continue
            payload_length = l4_length - header_length
            options_end = min(len(data), l4_offset + header_length)
            tsval, tsecr = parse_tcp_options(data[l4_offset + 20 : options_end])
            if direction == "up":
                target_ip, target_port = packet["source"], source_port
                remote_ip, remote_port = packet["destination"], destination_port
            else:
                target_ip, target_port = packet["destination"], destination_port
                remote_ip, remote_port = packet["source"], source_port
            base_flow_key = (packet["version"], target_ip, target_port, remote_ip, remote_port)
            syn = bool(flags & 0x02)
            ack_flag = bool(flags & 0x10)
            fin = bool(flags & 0x01)
            if direction == "up" and syn and not ack_flag and base_flow_key in flows:
                flow_generations[base_flow_key] += 1
                del flows[base_flow_key]
            generation = flow_generations[base_flow_key]
            flow_id = hashlib.sha256(("|".join(map(str, base_flow_key)) + f"|{generation}").encode()).hexdigest()[:10]
            state = flows.setdefault(
                base_flow_key,
                new_flow_state(flow_id, packet["version"], target_port, remote_port),
            )
            relative_time = timestamp - (capture_start or timestamp)

            segment_consumed = payload_length + int(syn) + int(fin)
            retransmitted = False
            if payload_length > 0:
                tcp_data_packets[direction] += 1
            if segment_consumed > 0:
                segment_key = (direction, sequence, segment_consumed, flags & 0x03)
                first_seen = state["seen_segments"].get(segment_key)
                if first_seen is None:
                    state["seen_segments"][segment_key] = timestamp
                elif timestamp - first_seen > 0.002:
                    retransmitted = True
                    event = {
                        "time_s": relative_time,
                        "flow_id": state["flow_id"],
                        "direction": direction,
                        "bytes": payload_length,
                        "delay_since_first_s": timestamp - first_seen,
                        "type": "timeout_like_retransmission"
                        if timestamp - first_seen >= 0.2
                        else "fast_like_retransmission",
                    }
                    if payload_length > 0:
                        retransmissions.append(event)
                        congestion_signals.append(event.copy())
                    else:
                        event["type"] = "handshake_or_close_retransmission"
                        handshake_retransmissions.append(event)

            if direction == "up" and tsval is not None:
                state["up_tsvals"][tsval] = timestamp
                if len(state["up_tsvals"]) > 4096:
                    state["up_tsvals"].pop(next(iter(state["up_tsvals"])))
            elif direction == "down" and tsecr and tsecr not in state["echoed_tsecr"]:
                sent_time = state["up_tsvals"].get(tsecr)
                if sent_time is not None:
                    sample = timestamp - sent_time
                    if 0.001 <= sample <= 5.0:
                        state["timestamp_rtt"].append((timestamp, sample))
                        state["echoed_tsecr"].add(tsecr)

            if direction == "up" and segment_consumed > 0 and not retransmitted:
                end_sequence = (sequence + segment_consumed) & 0xFFFFFFFF
                state["pending_up"][end_sequence] = (timestamp, False)
            if direction == "down" and ack_flag and state["pending_up"]:
                acknowledged = [
                    end_sequence
                    for end_sequence in state["pending_up"]
                    if sequence_leq(end_sequence, acknowledgment)
                ]
                if acknowledged:
                    candidate = max(acknowledged, key=lambda item: state["pending_up"][item][0])
                    sent_time, ambiguous = state["pending_up"][candidate]
                    sample = timestamp - sent_time
                    if not ambiguous and 0.001 <= sample <= 5.0:
                        state["ack_rtt"].append((timestamp, sample))
                    for end_sequence in acknowledged:
                        del state["pending_up"][end_sequence]

            # SYN/FIN/RST packets commonly carry a raw window of zero but are
            # not receiver backpressure events. Count only established ACK/data.
            is_zero = ack_flag and receive_window == 0 and not (flags & 0x07)
            if is_zero and not state["last_zero"][direction]:
                zero_windows.append(
                    {"time_s": relative_time, "flow_id": state["flow_id"], "direction": direction}
                )
            state["last_zero"][direction] = is_zero

            if direction == "down" and payload_length > 0 and not retransmitted:
                end_sequence = (sequence + payload_length) & 0xFFFFFFFF
                state["down_outstanding"][(sequence, end_sequence)] = payload_length
            if direction == "up" and ack_flag:
                current_flight = sum(state["down_outstanding"].values())
                if payload_length == 0 and state["last_target_ack"] == acknowledgment:
                    state["duplicate_ack_count"] += 1
                    if state["duplicate_ack_count"] == 3:
                        congestion_signals.append(
                            {
                                "time_s": relative_time,
                                "flow_id": state["flow_id"],
                                "direction": "down",
                                "bytes_in_flight_estimate": current_flight,
                                "type": "three_duplicate_acks",
                            }
                        )
                else:
                    state["last_target_ack"] = acknowledgment
                    state["duplicate_ack_count"] = 0
                acknowledged_down = [
                    segment
                    for segment in state["down_outstanding"]
                    if sequence_leq(segment[1], acknowledgment)
                ]
                for segment in acknowledged_down:
                    del state["down_outstanding"][segment]

    timestamp_rtt = sorted(
        (sample for state in flows.values() for sample in state["timestamp_rtt"]), key=lambda item: item[0]
    )
    ack_rtt = sorted((sample for state in flows.values() for sample in state["ack_rtt"]), key=lambda item: item[0])
    selected_method = "tcp_timestamp_echo" if len(timestamp_rtt) >= 3 else "tcp_ack_or_handshake"
    selected_pairs = timestamp_rtt if len(timestamp_rtt) >= 3 else ack_rtt
    selected_ms = [sample * 1000.0 for _, sample in selected_pairs]
    rtt = value_stats(selected_ms)
    rtt["successive_jitter_ms"] = mean_successive_delta(selected_ms)
    rtt["method"] = selected_method
    rtt["timestamp_sample_count"] = len(timestamp_rtt)
    rtt["ack_sample_count"] = len(ack_rtt)

    protocol_result = {
        direction: {name: dict(values) for name, values in groups.items()}
        for direction, groups in protocol.items()
    }
    return {
        "raw_packets": raw_packets,
        "parsed_target_ip_packets": parsed_packets,
        "duplicate_tap_packets_removed": duplicate_tap_packets,
        "duration_s": (capture_end - capture_start) if capture_start is not None and capture_end else 0.0,
        "protocol": protocol_result,
        "tcp_flow_count": len(flows),
        "tcp_data_packets": tcp_data_packets,
        "tcp_rtt_ms": rtt,
        "target_retransmission_count": len(retransmissions),
        "target_retransmission_ratio": len(retransmissions) / sum(tcp_data_packets.values())
        if sum(tcp_data_packets.values())
        else None,
        "retransmission_events": retransmissions[:100],
        "handshake_or_close_retransmission_count": len(handshake_retransmissions),
        "handshake_or_close_retransmission_events": handshake_retransmissions[:100],
        "zero_window_event_count": len(zero_windows),
        "terminal_zero_window_event_count": sum(event["direction"] == "up" for event in zero_windows),
        "remote_zero_window_event_count": sum(event["direction"] == "down" for event in zero_windows),
        "zero_window_events": zero_windows[:100],
        "direct_cwnd_observable": False,
        "congestion_control_signal_count": len(congestion_signals),
        "congestion_control_signals": congestion_signals[:100],
    }


def parse_capture_log(path: Path) -> dict[str, int | None]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    result: dict[str, int | None] = {"captured": None, "received_by_filter": None, "dropped_by_kernel": None}
    patterns = {
        "captured": r"(\d+) packets captured",
        "received_by_filter": r"(\d+) packets received by filter",
        "dropped_by_kernel": r"(\d+) packets dropped by kernel",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            result[key] = int(match.group(1))
    return result


def selected_kernel_deltas(all_deltas: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    wanted = {
        "Ip": {"InHdrErrors", "InAddrErrors", "InDiscards", "OutDiscards", "OutNoRoutes"},
        "Tcp": {"RetransSegs", "InErrs", "OutRsts"},
        "TcpExt": {
            "TCPFastRetrans",
            "TCPTimeouts",
            "TCPLossProbes",
            "TCPFromZeroWindowAdv",
            "TCPToZeroWindowAdv",
            "TCPWantZeroWindowAdv",
            "TCPZeroWindowDrop",
            "TCPRcvQDrop",
            "TCPBacklogDrop",
        },
    }
    return {
        section: {key: value for key, value in all_deltas.get(section, {}).items() if key in keys}
        for section, keys in wanted.items()
    }


def assess_client_count(adb: AdbClient, target_mac: str) -> dict[str, Any]:
    clients = parse_hostapd_clients(adb)
    return {
        "associated_client_count_end": len(clients),
        "target_still_associated": any(entry["mac"] == target_mac for entry in clients),
    }


def probe_fast_path(adb: AdbClient) -> dict[str, Any]:
    module_text = adb.shell("cat /proc/modules 2>/dev/null", timeout=10, check=False)
    loaded = {line.split()[0] for line in module_text.splitlines() if line.split()}
    relevant = sorted(
        loaded
        & {
            "hw_nat",
            "tops",
            "mtk_warp",
            "mtk_wed",
            "nf_flow_table",
            "nft_flow_offload",
        }
    )
    return {
        "hardware_fast_path_confirmed": bool(loaded & {"hw_nat", "tops", "mtk_warp", "mtk_wed"}),
        "loaded_modules": relevant,
    }


def add_capture_coverage(summary: dict[str, Any]) -> None:
    metadata = summary["metadata"]
    wan_interface = metadata.get("wan_interface", "ccmni3")
    interfaces = summary.get("interface_counter_deltas", {})
    wifi_bytes = interfaces.get(metadata["wifi_interface"], {}).get("tx_bytes")
    bridge_bytes = interfaces.get("br-lan", {}).get("tx_bytes")
    wan_bytes = interfaces.get(wan_interface, {}).get("rx_bytes")
    protocol_down = summary.get("pcap", {}).get("protocol", {}).get("down", {})
    pcap_bytes = sum(
        values.get("bytes", 0)
        for name, values in protocol_down.items()
        if name in {"tcp", "udp", "other"}
    )
    summary["capture_coverage"] = {
        "pcap_down_bytes": pcap_bytes,
        "bridge_tx_bytes": bridge_bytes,
        "wifi_tx_bytes": wifi_bytes,
        "wan_rx_bytes": wan_bytes,
        "pcap_vs_wifi_ratio": pcap_bytes / wifi_bytes if wifi_bytes else None,
        "bridge_vs_wifi_ratio": bridge_bytes / wifi_bytes if wifi_bytes else None,
        "wan_vs_wifi_ratio": wan_bytes / wifi_bytes if wifi_bytes else None,
    }


def reanalyze_evidence(adb: AdbClient, output: Path) -> dict[str, Any]:
    summary_path = output / "summary.json"
    if not summary_path.is_file():
        raise CollectionError(f"missing evidence summary: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    target_mac = summary.get("metadata", {}).get("target_mac")
    if not target_mac:
        raise CollectionError("evidence metadata does not contain target_mac")
    summary["pcap"] = analyze_pcap(output / "target_headers.pcap", target_mac)
    summary["fast_path_probe"] = probe_fast_path(adb)
    add_capture_coverage(summary)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, output)
    return summary


def write_report(summary: dict[str, Any], output: Path) -> None:
    metadata = summary["metadata"]
    wan_interface = metadata.get("wan_interface", "ccmni3")
    station = summary["station"]
    down = station["downlink_mbps"]
    down5 = station["downlink_5s_mbps"]
    ping = summary["ip_probe"]
    tcp = summary["pcap"]
    coverage = summary.get("capture_coverage", {})
    fast_path = summary.get("fast_path_probe", {})
    interface_delta = summary["interface_counter_deltas"]
    wifi_delta = interface_delta.get(metadata["wifi_interface"], {})
    wan_delta = interface_delta.get(wan_interface, {})
    protocol_down = tcp.get("protocol", {}).get("down", {})
    down_wire_bytes = sum(item.get("bytes", 0) for name, item in protocol_down.items() if name in {"tcp", "udp", "other"})
    tcp_down_bytes = protocol_down.get("tcp", {}).get("bytes", 0)
    udp443_down_bytes = protocol_down.get("udp_443_candidate", {}).get("bytes", 0)
    tcp_share = tcp_down_bytes * 100.0 / down_wire_bytes if down_wire_bytes else None
    udp443_share = udp443_down_bytes * 100.0 / down_wire_bytes if down_wire_bytes else None
    rtt = tcp["tcp_rtt_ms"]
    p95_minus_p5 = None
    if down.get("p95") is not None and down.get("p5") is not None:
        p95_minus_p5 = down["p95"] - down["p5"]

    lines = [
        "# RG660MK-EU 单终端视频网络第一层采集报告",
        "",
        f"- 采集时间：{metadata['started_at']}",
        f"- 采集时长：{fmt_num(station.get('duration_s'), 1, ' s')}",
        f"- 终端：`{metadata['hostname']}` / `{mask_ip(metadata.get('target_ip'))}` / `{mask_mac(metadata['target_mac'])}`",
        f"- 接入：`{metadata['wifi_interface']}`，蜂窝出口：`{wan_interface}`，采集开始/结束关联终端数：{metadata['associated_client_count_start']}/{metadata['associated_client_count_end']}",
        f"- 方法：ADB 只读采样 + 单终端 128-byte 包头流式采集；CPE 端未写文件、未改网络/防火墙/offload。",
        "",
        "## 1. 单终端吞吐",
        "",
        "吞吐主口径为唯一关联终端所在 Wi-Fi 接口的 TX/RX 字节差分；TX 即终端下行。",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 下行平均吞吐 | {fmt_num(down.get('mean'), 3, ' Mbit/s')} |",
        f"| 下行峰值吞吐（实际平均采样间隔 {fmt_num(station['actual_interval_s'].get('mean'), 2, ' s')}） | {fmt_num(down.get('max'), 3, ' Mbit/s')} |",
        f"| 下行 P5 / P50 / P95 | {fmt_num(down.get('p5'), 3)} / {fmt_num(down.get('p50'), 3)} / {fmt_num(down.get('p95'), 3)} Mbit/s |",
        f"| 波动幅度 P95-P5 | {fmt_num(p95_minus_p5, 3, ' Mbit/s')} |",
        f"| 标准差 / 变异系数 CV | {fmt_num(down.get('stddev'), 3, ' Mbit/s')} / {fmt_num(down.get('cv'), 3)} |",
        f"| 5 秒窗口 P5 / P50 / P95 | {fmt_num(down5.get('p5'), 3)} / {fmt_num(down5.get('p50'), 3)} / {fmt_num(down5.get('p95'), 3)} Mbit/s |",
        f"| Wi-Fi 信号均值 / 最差 | {fmt_num(station['signal_dbm'].get('mean'), 1)} / {fmt_num(station['signal_dbm'].get('min'), 1)} dBm |",
        "",
        "分片视频会呈现“突发下载—空闲缓存”节奏，因此 1 秒吞吐 CV 高不能单独判为卡顿；5 秒窗口更适合观察持续供给能力。",
        "",
        "## 2. IP 层与接口丢弃",
        "",
        f"- CPE 蜂窝 WAN 主动探测：发送 {ping.get('transmitted')}、接收 {ping.get('received')}，丢包率 **{fmt_num(ping.get('loss_percent'), 2, '%')}**。",
        f"- ICMP RTT 均值 / P95 / 相邻样本抖动：{fmt_num(ping['rtt_ms'].get('mean'), 2)} / {fmt_num(ping['rtt_ms'].get('p95'), 2)} / {fmt_num(ping['rtt_ms'].get('successive_jitter_ms'), 2)} ms。",
        f"- 蜂窝接口 RX/TX dropped 增量：{wan_delta.get('rx_dropped', '不可用')}/{wan_delta.get('tx_dropped', '不可用')}；errors 增量：{wan_delta.get('rx_errors', '不可用')}/{wan_delta.get('tx_errors', '不可用')}。",
        f"- Wi-Fi 驱动 RX/TX dropped 增量：{wifi_delta.get('rx_dropped', '不可用')}/{wifi_delta.get('tx_dropped', '不可用')}。该 MTK 驱动计数可能包含无线失败/重试，不能直接当作端到端 IP 丢包率。",
        f"- qdisc dropped 增量：{summary['qdisc_drop_deltas']}。",
        "",
        "主动 ICMP 从 CPE 发起，代表 CPE→公网路径，不等同于手机应用端到端丢包；目标业务的 TCP 重传是补充证据。",
        "",
        "## 3. TCP 层",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 被动 RTT 样本方法 / 数量 | {rtt.get('method')} / {rtt.get('count')} |",
        f"| TCP RTT 均值 / P95 | {fmt_num(rtt.get('mean'), 2)} / {fmt_num(rtt.get('p95'), 2)} ms |",
        f"| TCP RTT 抖动（相邻 RTT 绝对差均值） | {fmt_num(rtt.get('successive_jitter_ms'), 2, ' ms')} |",
        f"| 可见 TCP 数据重传 | {tcp.get('target_retransmission_count')} 次 |",
        f"| 可见 TCP 数据重传占数据段比例 | {fmt_num(None if tcp.get('target_retransmission_ratio') is None else tcp['target_retransmission_ratio'] * 100, 3, '%')} |",
        f"| SYN/FIN 等握手或关闭重试 | {tcp.get('handshake_or_close_retransmission_count')} 次 |",
        f"| 手机通告零窗口 / 远端通告零窗口 | {tcp.get('terminal_zero_window_event_count')} / {tcp.get('remote_zero_window_event_count')} 次 |",
        f"| TCP 流数量 | {tcp.get('tcp_flow_count')} |",
        "",
        f"下行包头可见流量中 TCP 字节占比约 {fmt_num(tcp_share, 1, '%')}；UDP/443（QUIC 候选）占比约 {fmt_num(udp443_share, 1, '%')}。UDP/443 只按端口识别，不属于 DPI 业务识别。",
        f"软件抓包对 Wi-Fi 下行字节的覆盖率约 {fmt_num(None if coverage.get('pcap_vs_wifi_ratio') is None else coverage['pcap_vs_wifi_ratio'] * 100, 1, '%')}；硬件快速转发确认={fast_path.get('hardware_fast_path_confirmed', False)}，相关模块={fast_path.get('loaded_modules', [])}。因此 RTT、重传、零窗口和拥塞线索只代表软件可见子集，不能外推至全部视频流。",
        "",
        "### 拥塞窗口骤降记录",
        "",
        "**无法直接读取真实 cwnd。** 手机业务连接是经 CPE 转发的 socket，TCP 状态属于手机和远端服务器，不属于 CPE 本地内核；`ss/TCP_INFO` 即使存在也看不到这些转发连接。以下仅记录可能触发发送端降窗的包级线索（重传、三次重复 ACK），不能表述为真实 cwnd 数值：",
        "",
        f"- 间接拥塞控制线索：{tcp.get('congestion_control_signal_count')} 条。",
    ]
    for event in tcp.get("congestion_control_signals", [])[:20]:
        details = f"，估算在途 {event['bytes_in_flight_estimate']} B" if "bytes_in_flight_estimate" in event else ""
        lines.append(
            f"  - t=+{event.get('time_s', 0):.3f}s，flow `{event.get('flow_id')}`，{event.get('type')}，方向 {event.get('direction')}{details}"
        )
    if not tcp.get("congestion_control_signals"):
        lines.append("  - 本次未观察到重传或三次重复 ACK 线索。")

    lines += [
        "",
        "## 4. 采集有效性与边界",
        "",
        f"- 原始包记录 {tcp.get('raw_packets')}，解析后的目标 IP 包 {tcp.get('parsed_target_ip_packets')}；修正 MTK/bridge tap 重复呈现 {tcp.get('duplicate_tap_packets_removed')} 包。",
        f"- 字节覆盖：pcap/Wi-Fi={fmt_num(None if coverage.get('pcap_vs_wifi_ratio') is None else coverage['pcap_vs_wifi_ratio'] * 100, 1, '%')}，br-lan/Wi-Fi={fmt_num(None if coverage.get('bridge_vs_wifi_ratio') is None else coverage['bridge_vs_wifi_ratio'] * 100, 1, '%')}；低覆盖由 HNAT/WARP/WED 快速路径造成。",
        f"- tcpdump 内核丢包：{summary['capture_log'].get('dropped_by_kernel') if summary['capture_log'].get('dropped_by_kernel') is not None else '不可用（该设备的 ADB exec-out 不回传远端 stderr）'}。",
        f"- 终端采样缺失：{station.get('station_missing_samples')} 次；采集结束仍关联：{metadata['target_still_associated']}。",
        "- 原始 pcap 仅保存每包前 128 字节，但仍含 IP/端口等元数据，应按敏感诊断证据管理。",
        "- `/proc/net/snmp` 的 TCP 计数只覆盖 CPE 本地 socket，不能替代本报告的目标终端包级分析。",
        "- 若视频采用 QUIC/HTTP3，TCP RTT、重传、零窗口指标不会覆盖该部分流量；需结合 UDP/443 占比判断适用性。",
        "- 本报告只评价网络是否具备导致卡顿的条件，不能感知手机解码、APP 缓存、片源或手机性能。",
        "",
        "## 5. 证据文件",
        "",
        "- `station_samples.csv`：1 秒接口/终端采样",
        "- `wan_ping.txt`：WAN 主动探测原始输出",
        "- `target_headers.pcap`：单终端 128-byte 包头",
        "- `snapshot_start.txt` / `snapshot_end.txt`：CPE 内核与接口计数",
        "- `summary.json`：结构化汇总",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect(adb: AdbClient, args: argparse.Namespace, output: Path) -> dict[str, Any]:
    state = adb.run(["get-state"], timeout=10)
    if state.stdout.strip() != "device":
        raise CollectionError("RG660MK-EU is not reachable over ADB")
    target = identify_target(adb, args)
    wifi_interface = target["interface"]
    wan_interface = identify_wan_interface(adb, args.wan_if, args.ping_target)
    target_mac = target["mac"]
    output.mkdir(parents=True, exist_ok=False)

    metadata: dict[str, Any] = {
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_requested_s": args.duration,
        "interval_s": args.interval,
        "target_mac": target_mac,
        "target_ip": target.get("ip"),
        "hostname": target.get("hostname", "unknown"),
        "wifi_interface": wifi_interface,
        "wan_interface": wan_interface,
        "associated_client_count_start": target["associated_client_count_start"],
        "ping_target": args.ping_target,
        "capture_snaplen": 128,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    start_snapshot = take_snapshot(adb, wifi_interface, wan_interface, output / "snapshot_start.txt")

    pcap_path = output / "target_headers.pcap"
    capture_log_path = output / "capture.stderr.log"
    samples_path = output / "station_samples.csv"
    samples_log_path = output / "station_samples.stderr.log"
    ping_path = output / "wan_ping.txt"

    capture: subprocess.Popen[Any] | None = None
    sampler: subprocess.Popen[Any] | None = None
    ping: subprocess.Popen[Any] | None = None
    remote_capture_pid: int | None = None
    handles: list[Any] = []
    try:
        pcap_handle = pcap_path.open("wb")
        capture_log_handle = capture_log_path.open("wb")
        sample_handle = samples_path.open("wb")
        sample_log_handle = samples_log_path.open("wb")
        ping_handle = ping_path.open("wb")
        handles += [pcap_handle, capture_log_handle, sample_handle, sample_log_handle, ping_handle]

        capture_filter = f"ether host {target_mac}"
        existing_capture_pids = find_remote_capture_pids(adb, target_mac)
        if existing_capture_pids:
            raise CollectionError(
                f"an existing tcpdump already targets {mask_mac(target_mac)}: {existing_capture_pids}"
            )
        capture_command = (
            f"exec tcpdump -i {shlex.quote(wifi_interface)} -U -n -s 128 -w - "
            f"{shlex.quote(capture_filter)} 2>/dev/null"
        )
        capture = adb.popen(["exec-out", "sh", "-c", capture_command], stdout=pcap_handle, stderr=capture_log_handle)
        for _ in range(20):
            time.sleep(0.1)
            capture_pids = find_remote_capture_pids(adb, target_mac)
            if len(capture_pids) == 1:
                remote_capture_pid = capture_pids[0]
                break
            if capture.poll() is not None:
                break
        if capture.poll() is not None:
            raise CollectionError("tcpdump exited before collection started")
        if remote_capture_pid is None:
            raise CollectionError("could not identify the remote tcpdump PID")

        sampler_command = build_sampler_command(
            args.duration,
            args.interval,
            wifi_interface,
            wan_interface,
            target_mac,
        )
        sampler = adb.popen(["exec-out", "sh", "-c", sampler_command], stdout=sample_handle, stderr=sample_log_handle)
        ping_count = max(1, math.ceil(args.duration / args.interval))
        ping_command = (
            f"exec ping -n -I {shlex.quote(wan_interface)} -c {ping_count} -i {args.interval} -W 2 "
            f"{shlex.quote(args.ping_target)}"
        )
        ping = adb.popen(["exec-out", "sh", "-c", ping_command], stdout=ping_handle, stderr=subprocess.STDOUT)

        print(
            f"COLLECTION_STARTED duration={args.duration}s target={mask_mac(target_mac)} "
            f"interface={wifi_interface} wan={wan_interface}",
            flush=True,
        )
        deadline = time.monotonic() + args.duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if capture.poll() is not None:
                raise CollectionError("tcpdump stopped unexpectedly during collection")
            if sampler.poll() is not None and remaining > args.interval + 2:
                raise CollectionError("station sampler stopped unexpectedly during collection")
            time.sleep(min(1.0, remaining))
    finally:
        stop_process(ping)
        stop_process(sampler)
        # Signal only the tcpdump PID started by this run. This flushes the pcap
        # and returns tcpdump's captured/filter/drop counters over stderr.
        if remote_capture_pid is not None:
            adb.shell(f"kill -INT {remote_capture_pid} 2>/dev/null", timeout=10, check=False)
        else:
            cleanup_remote_capture(adb, target_mac)
        if capture is not None and capture.poll() is None:
            try:
                capture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_process(capture)
        stop_process(capture)
        for handle in handles:
            handle.close()
        cleanup_remote_capture(adb, target_mac)

    end_snapshot = take_snapshot(adb, wifi_interface, wan_interface, output / "snapshot_end.txt")
    end_client = assess_client_count(adb, target_mac)
    metadata.update(end_client)
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    start_proc = parse_proc_tables(start_snapshot)
    end_proc = parse_proc_tables(end_snapshot)
    start_interfaces = parse_interface_counters(start_snapshot)
    end_interfaces = parse_interface_counters(end_snapshot)
    interface_deltas = nested_counter_delta(start_interfaces, end_interfaces)
    qdisc_start = parse_qdisc_drops(start_snapshot)
    qdisc_end = parse_qdisc_drops(end_snapshot)
    qdisc_deltas = {
        interface: qdisc_end.get(interface, 0) - qdisc_start.get(interface, 0)
        for interface in sorted(set(qdisc_start) | set(qdisc_end))
    }

    summary: dict[str, Any] = {
        "metadata": metadata,
        "station": analyze_station_samples(samples_path),
        "ip_probe": parse_ping(ping_path),
        "pcap": analyze_pcap(pcap_path, target_mac),
        "capture_log": parse_capture_log(capture_log_path),
        "kernel_counter_deltas": selected_kernel_deltas(nested_counter_delta(start_proc, end_proc)),
        "interface_counter_deltas": interface_deltas,
        "qdisc_drop_deltas": qdisc_deltas,
        "fast_path_probe": probe_fast_path(adb),
    }
    add_capture_coverage(summary)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, output)
    return summary


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect terminal-scoped L1 video network evidence from RG660MK-EU over ADB."
    )
    parser.add_argument("--duration", type=int, default=180, help="collection duration in seconds (5-1800)")
    parser.add_argument("--interval", type=int, default=1, help="counter and ping interval in seconds (1-10)")
    parser.add_argument("--target-mac", help="associated terminal MAC; auto-detected when exactly one is online")
    parser.add_argument("--target-ip", help="terminal IPv4/IPv6 metadata override")
    parser.add_argument("--wifi-if", help="expected AP interface, for example rai0")
    parser.add_argument("--wan-if", help="expected WAN interface; auto-detected from the active IPv4 route")
    parser.add_argument("--ping-target", default="223.5.5.5", help="public IP probe target")
    parser.add_argument("--serial", help="ADB serial when multiple devices are attached")
    parser.add_argument("--adb", default="adb", help="ADB executable")
    parser.add_argument("--output-dir", type=Path, help="new evidence directory")
    parser.add_argument(
        "--reanalyze",
        type=Path,
        help="recompute pcap metrics and report for an existing evidence directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        validate_inputs(args)
        adb = AdbClient(args.adb, args.serial)
        if args.reanalyze:
            output = args.reanalyze.expanduser().resolve()
            summary = reanalyze_evidence(adb, output)
        else:
            if args.output_dir:
                output = args.output_dir.expanduser().resolve()
            else:
                root = Path(__file__).resolve().parents[1]
                timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                output = root / "reports" / "evidence" / f"video_qoe_l1_{timestamp}"
            summary = collect(adb, args, output)
        down = summary["station"]["downlink_mbps"]
        print(
            "COLLECTION_COMPLETE "
            f"output={output} avg_down={fmt_num(down.get('mean'), 3)}Mbps "
            f"peak_down={fmt_num(down.get('max'), 3)}Mbps "
            f"ip_loss={fmt_num(summary['ip_probe'].get('loss_percent'), 2)}% "
            f"tcp_retrans={summary['pcap'].get('target_retransmission_count')}",
            flush=True,
        )
        return 0
    except (CollectionError, OSError, subprocess.SubprocessError) as exc:
        print(f"COLLECTION_FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
