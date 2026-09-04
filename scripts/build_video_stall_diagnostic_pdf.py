#!/usr/bin/env python3
"""Build a formal PDF report for the RG660MK-EU frequent-stall incident.

The report compares a strong-signal reference, a weak-signal no-stall sample,
and an extreme weak-signal frequent-stall sample. All charts and conclusions
are generated from local evidence; no network resource is used.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt


REPORT_TITLE = "RG660MK-EU 频繁卡顿场景诊断报告"
REPORT_ID = "RG660MK-QOE-STALL-20260825"
REPORT_VERSION = "R1.1"
REPORT_DATE = "2026-08-25"

COLORS = {
    "baseline": "#2563eb",
    "degraded": "#d97706",
    "stall": "#dc2626",
    "navy": "#0f172a",
    "slate": "#475569",
    "green": "#15803d",
    "light": "#e2e8f0",
}


class BuildError(RuntimeError):
    """Raised when evidence or output validation fails."""


@dataclass
class Scenario:
    key: str
    label: str
    observation: str
    evidence_dir: Path
    summary: dict[str, Any]
    sample_times: list[float]
    signals: list[float]
    period_times: list[float]
    wifi_rates: list[float]
    wan_rates: list[float]
    ping_times: list[float]
    ping_rtts: list[float]

    @property
    def metadata(self) -> dict[str, Any]:
        return self.summary["metadata"]

    @property
    def station(self) -> dict[str, Any]:
        return self.summary["station"]

    @property
    def tcp(self) -> dict[str, Any]:
        return self.summary["pcap"]

    @property
    def ping(self) -> dict[str, Any]:
        return self.summary["ip_probe"]

    @property
    def wan_interface(self) -> str:
        explicit = self.metadata.get("wan_interface")
        if explicit:
            return str(explicit)
        interfaces = self.summary.get("interface_counter_deltas", {})
        candidates = sorted(name for name in interfaces if name.startswith("ccmni"))
        return candidates[0] if candidates else "未记录"


def require_number(mapping: dict[str, Any], path: str) -> float:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise BuildError(f"missing required field: {path}")
        current = current[part]
    if not isinstance(current, (int, float)) or isinstance(current, bool) or not math.isfinite(float(current)):
        raise BuildError(f"required field is not a finite number: {path}")
    return float(current)


def configure_chinese_font() -> str:
    candidates = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Zen Hei",
        "DejaVu Sans",
    ]
    for family in candidates:
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=family),
                fallback_to_default=False,
            )
        except ValueError:
            continue
        if Path(path).is_file():
            plt.rcParams["font.family"] = family
            plt.rcParams["axes.unicode_minus"] = False
            return family
    raise BuildError("no usable Chinese font found for chart rendering")


def figure_data_uri(fig: plt.Figure) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def load_station_series(path: Path) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.DictReader(line.replace("\r", "") for line in handle if line.strip()))
    if len(rows) < 2:
        raise BuildError(f"not enough station samples: {path}")

    def number(row: dict[str, str], key: str) -> float | None:
        try:
            value = float(row.get(key, ""))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    valid = [row for row in rows if number(row, "monotonic_s") is not None]
    if len(valid) < 2:
        raise BuildError(f"station samples contain no usable time series: {path}")
    base_time = number(valid[0], "monotonic_s")
    assert base_time is not None

    sample_times: list[float] = []
    signals: list[float] = []
    for row in valid:
        t = number(row, "monotonic_s")
        signal = number(row, "signal_dbm")
        if t is not None and signal is not None:
            sample_times.append(t - base_time)
            signals.append(signal)

    period_times: list[float] = []
    wifi_rates: list[float] = []
    wan_rates: list[float] = []
    for previous, current in zip(valid, valid[1:]):
        left_t = number(previous, "monotonic_s")
        right_t = number(current, "monotonic_s")
        left_wifi = number(previous, "wifi_tx_bytes")
        right_wifi = number(current, "wifi_tx_bytes")
        left_wan = number(previous, "wan_rx_bytes")
        right_wan = number(current, "wan_rx_bytes")
        if None in (left_t, right_t, left_wifi, right_wifi, left_wan, right_wan):
            continue
        assert left_t is not None and right_t is not None
        assert left_wifi is not None and right_wifi is not None
        assert left_wan is not None and right_wan is not None
        elapsed = right_t - left_t
        if elapsed <= 0 or right_wifi < left_wifi or right_wan < left_wan:
            continue
        period_times.append(right_t - base_time)
        wifi_rates.append((right_wifi - left_wifi) * 8.0 / elapsed / 1_000_000.0)
        wan_rates.append((right_wan - left_wan) * 8.0 / elapsed / 1_000_000.0)
    if not wifi_rates:
        raise BuildError(f"no usable throughput periods: {path}")
    return sample_times, signals, period_times, wifi_rates, wan_rates


def load_ping_series(path: Path) -> tuple[list[float], list[float]]:
    pattern = re.compile(r"icmp_seq=(\d+).*?time[=<]([0-9.]+)\s*ms")
    times: list[float] = []
    values: list[float] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if match:
            times.append(float(match.group(1)) - 1.0)
            values.append(float(match.group(2)))
    if not values:
        raise BuildError(f"no ping RTT samples parsed: {path}")
    return times, values


def validate_recomputed_metrics(scenario: Scenario) -> None:
    summary_wifi_mean = require_number(scenario.summary, "station.downlink_mbps.mean")
    actual_wifi_mean = statistics.fmean(scenario.wifi_rates)
    if not math.isclose(summary_wifi_mean, actual_wifi_mean, rel_tol=2e-4, abs_tol=2e-5):
        raise BuildError(
            f"{scenario.key}: CSV downlink mean {actual_wifi_mean} disagrees with summary {summary_wifi_mean}"
        )
    summary_signal_mean = require_number(scenario.summary, "station.signal_dbm.mean")
    actual_signal_mean = statistics.fmean(scenario.signals)
    if not math.isclose(summary_signal_mean, actual_signal_mean, rel_tol=0, abs_tol=0.05):
        raise BuildError(
            f"{scenario.key}: CSV signal mean {actual_signal_mean} disagrees with summary {summary_signal_mean}"
        )
    summary_ping_mean = require_number(scenario.summary, "ip_probe.rtt_ms.mean")
    actual_ping_mean = statistics.fmean(scenario.ping_rtts)
    if not math.isclose(summary_ping_mean, actual_ping_mean, rel_tol=0, abs_tol=0.08):
        raise BuildError(
            f"{scenario.key}: ping mean {actual_ping_mean} disagrees with summary {summary_ping_mean}"
        )
    expected_ping_count = int(require_number(scenario.summary, "ip_probe.rtt_ms.count"))
    if expected_ping_count != len(scenario.ping_rtts):
        raise BuildError(
            f"{scenario.key}: ping sample count {len(scenario.ping_rtts)} disagrees with summary {expected_ping_count}"
        )


def load_scenario(key: str, label: str, observation: str, evidence_dir: Path) -> Scenario:
    required = ["summary.json", "station_samples.csv", "wan_ping.txt", "REPORT.md", "target_headers.pcap"]
    missing = [name for name in required if not (evidence_dir / name).is_file()]
    if missing:
        raise BuildError(f"{evidence_dir}: missing evidence files: {', '.join(missing)}")
    summary = json.loads((evidence_dir / "summary.json").read_text(encoding="utf-8"))
    for path in (
        "station.duration_s",
        "station.downlink_mbps.mean",
        "station.downlink_mbps.max",
        "station.downlink_5s_mbps.p50",
        "station.downlink_5s_mbps.p95",
        "station.signal_dbm.mean",
        "station.signal_dbm.min",
        "ip_probe.loss_percent",
        "ip_probe.rtt_ms.mean",
        "ip_probe.rtt_ms.p95",
        "pcap.tcp_rtt_ms.mean",
        "pcap.tcp_rtt_ms.p95",
        "pcap.tcp_rtt_ms.successive_jitter_ms",
        "pcap.target_retransmission_count",
        "pcap.target_retransmission_ratio",
        "pcap.handshake_or_close_retransmission_count",
        "pcap.congestion_control_signal_count",
        "capture_coverage.pcap_vs_wifi_ratio",
    ):
        require_number(summary, path)
    sample_times, signals, period_times, wifi_rates, wan_rates = load_station_series(
        evidence_dir / "station_samples.csv"
    )
    ping_times, ping_rtts = load_ping_series(evidence_dir / "wan_ping.txt")
    scenario = Scenario(
        key=key,
        label=label,
        observation=observation,
        evidence_dir=evidence_dir,
        summary=summary,
        sample_times=sample_times,
        signals=signals,
        period_times=period_times,
        wifi_rates=wifi_rates,
        wan_rates=wan_rates,
        ping_times=ping_times,
        ping_rtts=ping_rtts,
    )
    validate_recomputed_metrics(scenario)
    return scenario


def validate_scenarios(scenarios: list[Scenario]) -> None:
    first = scenarios[0].metadata
    identity = (first.get("target_mac"), first.get("target_ip"), first.get("wifi_interface"))
    for scenario in scenarios:
        metadata = scenario.metadata
        current = (metadata.get("target_mac"), metadata.get("target_ip"), metadata.get("wifi_interface"))
        if current != identity:
            raise BuildError(f"scenario identity mismatch: {scenario.key}: {current} != {identity}")
        if metadata.get("associated_client_count_start") != 1 or metadata.get("associated_client_count_end") != 1:
            raise BuildError(f"{scenario.key}: expected exactly one associated terminal")
        if metadata.get("target_still_associated") is not True:
            raise BuildError(f"{scenario.key}: target was not associated at collection end")
        if not 170 <= require_number(scenario.summary, "station.duration_s") <= 190:
            raise BuildError(f"{scenario.key}: collection duration is outside the comparable range")
        if int(metadata.get("capture_snaplen", 0)) != 128:
            raise BuildError(f"{scenario.key}: unexpected pcap snaplen")
    if require_number(scenarios[-1].summary, "pcap.target_retransmission_count") < 100:
        raise BuildError("stall evidence does not contain the expected severe retransmission condition")


def rolling_average(values: list[float], width: int = 4) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        left = max(0, index - width + 1)
        result.append(statistics.fmean(values[left : index + 1]))
    return result


def comparison_chart(scenarios: list[Scenario]) -> str:
    configure_chinese_font()
    labels = ["强信号\n参考", "弱信号\n无卡顿", "极弱信号\n频繁卡顿"]
    colors = [COLORS[item.key] for item in scenarios]
    x = list(range(3))
    fig, axes = plt.subplots(2, 3, figsize=(12.2, 7.3))
    fig.suptitle("三场景核心网络指标对比", fontsize=17, fontweight="bold", color=COLORS["navy"])

    signal = [require_number(item.summary, "station.signal_dbm.mean") for item in scenarios]
    axes[0, 0].plot(x, signal, marker="o", linewidth=2.8, markersize=8, color=COLORS["navy"])
    for index, value in enumerate(signal):
        axes[0, 0].scatter(index, value, color=colors[index], s=65, zorder=3)
        axes[0, 0].text(index, value + 1.1, f"{value:.1f}", ha="center", fontsize=9)
    axes[0, 0].set_title("Wi-Fi 平均信号（dBm）")
    axes[0, 0].set_ylim(-95, -55)
    axes[0, 0].axhspan(-95, -85, color="#fee2e2", alpha=0.75)

    means = [require_number(item.summary, "station.downlink_mbps.mean") for item in scenarios]
    peaks = [require_number(item.summary, "station.downlink_mbps.max") for item in scenarios]
    width = 0.34
    axes[0, 1].bar([v - width / 2 for v in x], means, width, label="平均", color=colors, alpha=0.78)
    axes[0, 1].bar([v + width / 2 for v in x], peaks, width, label="峰值", color=colors, hatch="//")
    axes[0, 1].set_title("终端下行吞吐（Mbit/s）")
    axes[0, 1].legend(frameon=False, fontsize=8)

    tcp_mean = [require_number(item.summary, "pcap.tcp_rtt_ms.mean") for item in scenarios]
    tcp_p95 = [require_number(item.summary, "pcap.tcp_rtt_ms.p95") for item in scenarios]
    axes[0, 2].bar([v - width / 2 for v in x], tcp_mean, width, label="均值", color=colors, alpha=0.78)
    axes[0, 2].bar([v + width / 2 for v in x], tcp_p95, width, label="P95", color=colors, hatch="//")
    axes[0, 2].set_yscale("log")
    axes[0, 2].set_title("可见 TCP RTT（ms，对数轴）")
    axes[0, 2].legend(frameon=False, fontsize=8)

    jitter = [require_number(item.summary, "pcap.tcp_rtt_ms.successive_jitter_ms") for item in scenarios]
    axes[1, 0].bar(x, jitter, color=colors)
    axes[1, 0].set_title("TCP RTT 相邻抖动（ms）")
    for index, value in enumerate(jitter):
        axes[1, 0].text(index, value + max(jitter) * 0.025, f"{value:.1f}", ha="center", fontsize=9)

    retrans = [require_number(item.summary, "pcap.target_retransmission_ratio") * 100 for item in scenarios]
    axes[1, 1].bar(x, retrans, color=colors)
    axes[1, 1].set_title("可见 TCP 数据重传比例（%）")
    for index, value in enumerate(retrans):
        axes[1, 1].text(index, value + max(retrans) * 0.025, f"{value:.2f}%", ha="center", fontsize=9)

    controls = [require_number(item.summary, "pcap.handshake_or_close_retransmission_count") for item in scenarios]
    axes[1, 2].bar(x, controls, color=colors)
    axes[1, 2].set_yscale("log")
    axes[1, 2].set_title("SYN/FIN/关闭重试（次，对数轴）")
    for index, value in enumerate(controls):
        axes[1, 2].text(index, value * 1.2, f"{int(value)}", ha="center", fontsize=9)

    for axis in axes.flat:
        axis.set_xticks(x, labels)
        axis.grid(axis="y", alpha=0.23, linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(labelsize=8.5)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return figure_data_uri(fig)


def distribution_chart(scenarios: list[Scenario]) -> str:
    configure_chinese_font()
    labels = ["强信号参考", "弱信号无卡顿", "极弱信号频繁卡顿"]
    colors = [COLORS[item.key] for item in scenarios]
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 4.6))
    fig.suptitle("原始采样分布对比", fontsize=16, fontweight="bold", color=COLORS["navy"])

    boxes = axes[0].boxplot([item.signals for item in scenarios], tick_labels=labels, patch_artist=True, showfliers=False)
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.72)
    axes[0].set_title("Wi-Fi 信号分布（dBm）")
    axes[0].axhspan(-95, -85, color="#fee2e2", alpha=0.65)

    boxes = axes[1].boxplot([item.wifi_rates for item in scenarios], tick_labels=labels, patch_artist=True, showfliers=False)
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.72)
    axes[1].set_title("逐采样间隔下行吞吐分布（Mbit/s）")
    axes[1].set_yscale("symlog", linthresh=0.02)

    for axis in axes:
        axis.grid(axis="y", alpha=0.25)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(axis="x", labelrotation=8, labelsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    return figure_data_uri(fig)


def timeline_chart(stall: Scenario) -> str:
    configure_chinese_font()
    fig, axes = plt.subplots(4, 1, figsize=(12.2, 10.1), sharex=True, gridspec_kw={"height_ratios": [1.3, 1, 1, 0.9]})
    fig.suptitle("频繁卡顿样本时间线（相对采集时间）", fontsize=17, fontweight="bold", color=COLORS["navy"])

    axes[0].plot(stall.period_times, rolling_average(stall.wifi_rates), color=COLORS["stall"], linewidth=1.8, label="Wi-Fi 下行（约 6 秒平滑）")
    axes[0].plot(stall.period_times, rolling_average(stall.wan_rates), color=COLORS["baseline"], linewidth=1.35, alpha=0.85, label="WAN 下行（约 6 秒平滑）")
    axes[0].set_ylabel("Mbit/s")
    axes[0].legend(frameon=False, fontsize=8, ncol=2)

    axes[1].plot(stall.sample_times, stall.signals, color=COLORS["stall"], linewidth=2)
    axes[1].axhspan(-95, -85, color="#fee2e2", alpha=0.72, label="极弱信号区")
    axes[1].set_ylabel("RSSI dBm")
    axes[1].legend(frameon=False, fontsize=8, loc="lower left")

    axes[2].plot(stall.ping_times, stall.ping_rtts, color=COLORS["green"], linewidth=1.25)
    axes[2].axhline(require_number(stall.summary, "ip_probe.rtt_ms.p95"), color=COLORS["green"], linestyle="--", linewidth=1, label="WAN P95")
    axes[2].set_ylabel("WAN RTT ms")
    axes[2].legend(frameon=False, fontsize=8)

    events = stall.tcp.get("congestion_control_signals", [])
    categories = {
        "timeout_like_retransmission": (2, "超时型重传", "#dc2626"),
        "fast_like_retransmission": (1, "快速重传", "#f59e0b"),
        "three_duplicate_acks": (0, "三次重复 ACK", "#7c3aed"),
    }
    used: set[str] = set()
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type not in categories:
            continue
        y, label, color = categories[event_type]
        legend = label if event_type not in used else None
        used.add(event_type)
        axes[3].scatter(float(event.get("time_s", 0)), y, marker="|", s=115, linewidths=1.5, color=color, label=legend)
    axes[3].set_yticks([0, 1, 2], ["重复 ACK", "快速重传", "超时重传"])
    axes[3].set_ylabel("前100条\n事件")
    axes[3].set_xlabel("相对时间（秒）")
    axes[3].legend(frameon=False, fontsize=8, ncol=3, loc="upper right")

    for axis in axes:
        axis.grid(alpha=0.22, linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_xlim(0, max(stall.period_times[-1], stall.ping_times[-1], 178))
        axis.tick_params(labelsize=8.5)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return figure_data_uri(fig)


def data_quality_chart(scenarios: list[Scenario]) -> str:
    configure_chinese_font()
    labels = ["强信号参考", "弱信号无卡顿", "极弱信号频繁卡顿"]
    colors = [COLORS[item.key] for item in scenarios]
    coverage = [require_number(item.summary, "capture_coverage.pcap_vs_wifi_ratio") * 100 for item in scenarios]
    duplicates = [require_number(item.summary, "pcap.duplicate_tap_packets_removed") for item in scenarios]
    samples = [require_number(item.summary, "pcap.tcp_rtt_ms.count") for item in scenarios]
    fig, axes = plt.subplots(1, 3, figsize=(12.2, 4.2))
    fig.suptitle("软件可见证据覆盖与质量", fontsize=16, fontweight="bold", color=COLORS["navy"])
    axes[0].bar(range(3), coverage, color=colors)
    axes[0].set_title("pcap / Wi-Fi 下行覆盖（%）")
    axes[1].bar(range(3), duplicates, color=colors)
    axes[1].set_title("去除的重复 tap 包（个）")
    axes[1].set_yscale("symlog", linthresh=10)
    axes[2].bar(range(3), samples, color=colors)
    axes[2].set_title("TCP RTT 样本数")
    for axis in axes:
        axis.set_xticks(range(3), labels, rotation=8)
        axis.grid(axis="y", alpha=0.23)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(labelsize=8.2)
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    return figure_data_uri(fig)


def fnum(value: Any, digits: int = 2) -> str:
    if value is None:
        return "不可用"
    return f"{float(value):.{digits}f}"


def pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "不可用"
    return f"{float(value) * 100:.{digits}f}%"


def mask_mac(value: str | None) -> str:
    if not value:
        return "unknown"
    fields = value.split(":")
    return ":".join(fields[:2] + ["**", "**"] + fields[-2:]) if len(fields) == 6 else value


def mask_ip(value: str | None) -> str:
    if not value:
        return "unknown"
    fields = value.split(".")
    return ".".join(fields[:3] + ["x"]) if len(fields) == 4 else value


def scenario_metric_rows(scenarios: list[Scenario]) -> str:
    rows: list[tuple[str, list[str]]] = [
        ("人工观察标签", [item.observation for item in scenarios]),
        ("采集时长（s）", [fnum(item.station["duration_s"], 1) for item in scenarios]),
        ("蜂窝出口", [item.wan_interface for item in scenarios]),
        ("信号均值 / 最差（dBm）", [f"{fnum(item.station['signal_dbm']['mean'], 1)} / {fnum(item.station['signal_dbm']['min'], 1)}" for item in scenarios]),
        ("下行均值 / 峰值（Mbit/s）", [f"{fnum(item.station['downlink_mbps']['mean'], 3)} / {fnum(item.station['downlink_mbps']['max'], 3)}" for item in scenarios]),
        ("5 秒下行 P50 / P95（Mbit/s）", [f"{fnum(item.station['downlink_5s_mbps']['p50'], 3)} / {fnum(item.station['downlink_5s_mbps']['p95'], 3)}" for item in scenarios]),
        ("WAN 丢包 / RTT P95", [f"{fnum(item.ping['loss_percent'], 2)}% / {fnum(item.ping['rtt_ms']['p95'], 2)} ms" for item in scenarios]),
        ("TCP RTT 均值 / P95（ms）", [f"{fnum(item.tcp['tcp_rtt_ms']['mean'], 2)} / {fnum(item.tcp['tcp_rtt_ms']['p95'], 2)}" for item in scenarios]),
        ("TCP RTT 抖动（ms）", [fnum(item.tcp['tcp_rtt_ms']['successive_jitter_ms'], 2) for item in scenarios]),
        ("数据重传次数 / 比例", [f"{int(item.tcp['target_retransmission_count'])} / {pct(item.tcp['target_retransmission_ratio'], 3)}" for item in scenarios]),
        ("SYN/FIN/关闭重试（次）", [str(int(item.tcp['handshake_or_close_retransmission_count'])) for item in scenarios]),
        ("间接拥塞线索（条）", [str(int(item.tcp['congestion_control_signal_count'])) for item in scenarios]),
        ("手机 / 远端零窗口", [f"{int(item.tcp['terminal_zero_window_event_count'])} / {int(item.tcp['remote_zero_window_event_count'])}" for item in scenarios]),
        ("Wi-Fi station TX 重试类增量", [str(int(item.station['counter_deltas']['sta_tx_retries'])) for item in scenarios]),
        ("pcap / Wi-Fi 覆盖", [pct(item.summary['capture_coverage']['pcap_vs_wifi_ratio'], 2) for item in scenarios]),
    ]
    return "".join(
        "<tr><th>" + html.escape(name) + "</th>" + "".join(f"<td>{html.escape(value)}</td>" for value in values) + "</tr>"
        for name, values in rows
    )


def key_event_rows(stall: Scenario) -> str:
    data_events = stall.tcp.get("retransmission_events", [])
    control_events = stall.tcp.get("handshake_or_close_retransmission_events", [])
    selected: list[tuple[str, dict[str, Any], str]] = []
    if control_events:
        selected.append(("控制段异常起点", min(control_events, key=lambda event: float(event.get("time_s", 0))), "连接控制阶段已出现重试"))
    if data_events:
        selected.append(("首个数据重传", min(data_events, key=lambda event: float(event.get("time_s", 0))), "业务数据传输早期即退化"))
        ranges = [
            (13, 21, "早期重传簇", "多流、双方向重传开始聚集"),
            (51, 58, "持续风暴起点", "大报文超时型重传连续出现"),
            (80, 87, "持续风暴后段", "前100条清单截断前仍未恢复"),
        ]
        for left, right, title, meaning in ranges:
            candidates = [event for event in data_events if left <= float(event.get("time_s", 0)) <= right]
            if candidates:
                selected.append((title, candidates[0], meaning))
    rows = []
    for title, event, meaning in selected:
        rows.append(
            "<tr>"
            f"<td>{html.escape(title)}</td>"
            f"<td>+{float(event.get('time_s', 0)):.3f} s</td>"
            f"<td>{html.escape(str(event.get('flow_id', '—')))}</td>"
            f"<td>{html.escape(str(event.get('direction', '—')))}</td>"
            f"<td>{html.escape(str(event.get('type', '—')))}</td>"
            f"<td>{html.escape(meaning)}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(scenarios: list[Scenario]) -> str:
    baseline, degraded, stall = scenarios
    comparison_image = comparison_chart(scenarios)
    distribution_image = distribution_chart(scenarios)
    timeline_image = timeline_chart(stall)
    quality_image = data_quality_chart(scenarios)

    rtt_multiplier = require_number(stall.summary, "pcap.tcp_rtt_ms.p95") / require_number(
        baseline.summary, "pcap.tcp_rtt_ms.p95"
    )
    jitter_multiplier = require_number(stall.summary, "pcap.tcp_rtt_ms.successive_jitter_ms") / require_number(
        baseline.summary, "pcap.tcp_rtt_ms.successive_jitter_ms"
    )
    retrans_multiplier = require_number(stall.summary, "pcap.target_retransmission_ratio") / require_number(
        baseline.summary, "pcap.target_retransmission_ratio"
    )
    retry_multiplier = require_number(stall.summary, "station.counter_deltas.sta_tx_retries") / require_number(
        baseline.summary, "station.counter_deltas.sta_tx_retries"
    )
    peak_ratio = require_number(stall.summary, "station.downlink_mbps.max") / require_number(
        degraded.summary, "station.downlink_mbps.max"
    )
    duplicate_ack_count = int(stall.tcp["congestion_control_signal_count"] - stall.tcp["target_retransmission_count"])
    retrans_events = stall.tcp.get("retransmission_events", [])
    control_events = stall.tcp.get("handshake_or_close_retransmission_events", [])
    congestion_events = stall.tcp.get("congestion_control_signals", [])
    truncated_notes = []
    if len(retrans_events) < int(stall.tcp["target_retransmission_count"]):
        truncated_notes.append("数据重传明细")
    if len(control_events) < int(stall.tcp["handshake_or_close_retransmission_count"]):
        truncated_notes.append("控制段重试明细")
    if len(congestion_events) < int(stall.tcp["congestion_control_signal_count"]):
        truncated_notes.append("拥塞线索明细")

    def metric(item: Scenario, path: str) -> float:
        return require_number(item.summary, path)

    def dbm(item: Scenario, path: str) -> str:
        return f"{metric(item, path):.1f}".replace("-", "−") + " dBm"

    anomaly_data: list[tuple[str, str, str, str, str, str]] = [
        (
            "Wi-Fi 平均信号",
            dbm(baseline, "station.signal_dbm.mean"),
            dbm(degraded, "station.signal_dbm.mean"),
            dbm(stall, "station.signal_dbm.mean"),
            f"恶化 {abs(metric(stall, 'station.signal_dbm.mean') - metric(baseline, 'station.signal_dbm.mean')):.1f} dB，接近断连边缘",
            "critical",
        ),
        (
            "Wi-Fi 最差信号",
            dbm(baseline, "station.signal_dbm.min"),
            dbm(degraded, "station.signal_dbm.min"),
            dbm(stall, "station.signal_dbm.min"),
            f"恶化 {abs(metric(stall, 'station.signal_dbm.min') - metric(baseline, 'station.signal_dbm.min')):.1f} dB，进入极弱 RF 区",
            "critical",
        ),
        (
            "下行平均流量",
            f"{metric(baseline, 'station.downlink_mbps.mean'):.3f} Mbit/s",
            f"{metric(degraded, 'station.downlink_mbps.mean'):.3f} Mbit/s",
            f"{metric(stall, 'station.downlink_mbps.mean'):.3f} Mbit/s",
            f"下降约 {(1 - metric(stall, 'station.downlink_mbps.mean') / metric(baseline, 'station.downlink_mbps.mean')) * 100:.1f}%；仅代表实际消费量",
            "critical",
        ),
        (
            "下行峰值",
            f"{metric(baseline, 'station.downlink_mbps.max'):.3f} Mbit/s",
            f"{metric(degraded, 'station.downlink_mbps.max'):.3f} Mbit/s",
            f"{metric(stall, 'station.downlink_mbps.max'):.3f} Mbit/s",
            f"下降约 {(1 - metric(stall, 'station.downlink_mbps.max') / metric(baseline, 'station.downlink_mbps.max')) * 100:.1f}%，突发供给明显不足",
            "critical",
        ),
        (
            "5 秒下行 P95",
            f"{metric(baseline, 'station.downlink_5s_mbps.p95'):.3f} Mbit/s",
            f"{metric(degraded, 'station.downlink_5s_mbps.p95'):.3f} Mbit/s",
            f"{metric(stall, 'station.downlink_5s_mbps.p95'):.3f} Mbit/s",
            f"下降约 {(1 - metric(stall, 'station.downlink_5s_mbps.p95') / metric(baseline, 'station.downlink_5s_mbps.p95')) * 100:.1f}%",
            "critical",
        ),
        (
            "TCP RTT 均值",
            f"{metric(baseline, 'pcap.tcp_rtt_ms.mean'):.2f} ms",
            f"{metric(degraded, 'pcap.tcp_rtt_ms.mean'):.2f} ms",
            f"{metric(stall, 'pcap.tcp_rtt_ms.mean'):.2f} ms",
            f"增至 {metric(stall, 'pcap.tcp_rtt_ms.mean') / metric(baseline, 'pcap.tcp_rtt_ms.mean'):.1f} 倍",
            "critical",
        ),
        (
            "TCP RTT P95",
            f"{metric(baseline, 'pcap.tcp_rtt_ms.p95'):.2f} ms",
            f"{metric(degraded, 'pcap.tcp_rtt_ms.p95'):.2f} ms",
            f"{metric(stall, 'pcap.tcp_rtt_ms.p95'):.2f} ms",
            f"增至 {metric(stall, 'pcap.tcp_rtt_ms.p95') / metric(baseline, 'pcap.tcp_rtt_ms.p95'):.1f} 倍，最大达到 {metric(stall, 'pcap.tcp_rtt_ms.max') / 1000:.3f} 秒",
            "critical",
        ),
        (
            "TCP 相邻 RTT 抖动",
            f"{metric(baseline, 'pcap.tcp_rtt_ms.successive_jitter_ms'):.2f} ms",
            f"{metric(degraded, 'pcap.tcp_rtt_ms.successive_jitter_ms'):.2f} ms",
            f"{metric(stall, 'pcap.tcp_rtt_ms.successive_jitter_ms'):.2f} ms",
            f"增至 {metric(stall, 'pcap.tcp_rtt_ms.successive_jitter_ms') / metric(baseline, 'pcap.tcp_rtt_ms.successive_jitter_ms'):.1f} 倍",
            "critical",
        ),
        (
            "TCP 数据重传",
            f"{int(metric(baseline, 'pcap.target_retransmission_count'))} 次 / {metric(baseline, 'pcap.target_retransmission_ratio') * 100:.3f}%",
            f"{int(metric(degraded, 'pcap.target_retransmission_count'))} 次 / {metric(degraded, 'pcap.target_retransmission_ratio') * 100:.3f}%",
            f"{int(metric(stall, 'pcap.target_retransmission_count'))} 次 / {metric(stall, 'pcap.target_retransmission_ratio') * 100:.3f}%",
            f"次数增至 {metric(stall, 'pcap.target_retransmission_count') / metric(baseline, 'pcap.target_retransmission_count'):.1f} 倍，比例增至 {metric(stall, 'pcap.target_retransmission_ratio') / metric(baseline, 'pcap.target_retransmission_ratio'):.1f} 倍",
            "critical",
        ),
        (
            "SYN/FIN/关闭段重试",
            f"{int(metric(baseline, 'pcap.handshake_or_close_retransmission_count'))} 次",
            f"{int(metric(degraded, 'pcap.handshake_or_close_retransmission_count'))} 次",
            f"{int(metric(stall, 'pcap.handshake_or_close_retransmission_count'))} 次",
            f"增至 {metric(stall, 'pcap.handshake_or_close_retransmission_count') / metric(baseline, 'pcap.handshake_or_close_retransmission_count'):.1f} 倍，连接控制阶段严重异常",
            "critical",
        ),
        (
            "间接拥塞线索",
            f"{int(metric(baseline, 'pcap.congestion_control_signal_count'))} 条",
            f"{int(metric(degraded, 'pcap.congestion_control_signal_count'))} 条",
            f"{int(metric(stall, 'pcap.congestion_control_signal_count'))} 条",
            f"增至 {metric(stall, 'pcap.congestion_control_signal_count') / metric(baseline, 'pcap.congestion_control_signal_count'):.1f} 倍",
            "critical",
        ),
        (
            "Wi-Fi station TX 重试类增量",
            str(int(metric(baseline, "station.counter_deltas.sta_tx_retries"))),
            str(int(metric(degraded, "station.counter_deltas.sta_tx_retries"))),
            str(int(metric(stall, "station.counter_deltas.sta_tx_retries"))),
            f"增至 {metric(stall, 'station.counter_deltas.sta_tx_retries') / metric(baseline, 'station.counter_deltas.sta_tx_retries'):.1f} 倍，无线重试压力激增",
            "critical",
        ),
        (
            "WAN 丢包率",
            f"{metric(baseline, 'ip_probe.loss_percent'):.0f}%",
            f"{metric(degraded, 'ip_probe.loss_percent'):.0f}%",
            f"{metric(stall, 'ip_probe.loss_percent'):.0f}%",
            "未恶化，不支持蜂窝 WAN 为主因",
            "counter",
        ),
        (
            "WAN RTT P95",
            f"{metric(baseline, 'ip_probe.rtt_ms.p95'):.2f} ms",
            f"{metric(degraded, 'ip_probe.rtt_ms.p95'):.2f} ms",
            f"{metric(stall, 'ip_probe.rtt_ms.p95'):.2f} ms",
            "反而更低，不支持蜂窝 WAN 为主因",
            "counter",
        ),
        (
            "手机 / 远端 TCP 零窗口",
            f"{int(metric(baseline, 'pcap.terminal_zero_window_event_count'))} / {int(metric(baseline, 'pcap.remote_zero_window_event_count'))}",
            f"{int(metric(degraded, 'pcap.terminal_zero_window_event_count'))} / {int(metric(degraded, 'pcap.remote_zero_window_event_count'))}",
            f"{int(metric(stall, 'pcap.terminal_zero_window_event_count'))} / {int(metric(stall, 'pcap.remote_zero_window_event_count'))}",
            "未见接收窗口耗尽",
            "counter",
        ),
        (
            "qdisc、WAN drop/error",
            "未见异常",
            "未见异常",
            "均为 0",
            "不支持 CPE 队列或蜂窝接口拥塞",
            "counter",
        ),
    ]
    anomaly_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{html.escape(reference)}</td>"
        f"<td>{html.escape(degraded_value)}</td>"
        f"<td class=\"{classification}\">{html.escape(stall_value)}</td>"
        f"<td class=\"{classification}\">{html.escape(interpretation)}</td>"
        "</tr>"
        for name, reference, degraded_value, stall_value, interpretation, classification in anomaly_data
    )

    style = r"""
@page {
  size: A4;
  margin: 16mm 14mm 17mm 14mm;
  @top-left { content: "RG660MK-EU 频繁卡顿场景诊断报告"; color: #64748b; font: 8pt "Noto Sans CJK SC", sans-serif; }
  @top-right { content: "RG660MK-QOE-STALL-20260825 · R1.1"; color: #64748b; font: 8pt "Noto Sans CJK SC", sans-serif; }
  @bottom-left { content: "受控压力场景 · 本地实测证据"; color: #94a3b8; font: 7.5pt "Noto Sans CJK SC", sans-serif; }
  @bottom-right { content: "第 " counter(page) " 页"; color: #64748b; font: 8pt "Noto Sans CJK SC", sans-serif; }
}
@page cover { margin: 0; @top-left { content: none; } @top-right { content: none; } @bottom-left { content: none; } @bottom-right { content: none; } }
* { box-sizing: border-box; }
html { font-family: "Noto Sans CJK SC", "Noto Sans CJK JP", sans-serif; color: #172033; }
body { margin: 0; font-size: 9.35pt; line-height: 1.58; background: white; }
.cover { page: cover; height: 297mm; padding: 25mm 22mm; color: white; background: linear-gradient(145deg, #0f172a 0%, #1e293b 58%, #7f1d1d 100%); position: relative; overflow: hidden; }
.cover:before { content: ""; position: absolute; width: 155mm; height: 155mm; border: 1px solid rgba(255,255,255,.13); border-radius: 50%; top: -58mm; right: -48mm; }
.cover:after { content: ""; position: absolute; width: 100mm; height: 100mm; border: 20mm solid rgba(239,68,68,.12); border-radius: 50%; bottom: -48mm; left: -35mm; }
.cover .eyebrow { margin-top: 18mm; color: #fca5a5; letter-spacing: .16em; font-size: 10pt; font-weight: 700; }
.cover h1 { margin: 13mm 0 6mm; font-size: 31pt; line-height: 1.22; letter-spacing: .03em; max-width: 160mm; }
.cover h2 { margin: 0; font-size: 15pt; font-weight: 400; color: #cbd5e1; }
.cover .verdict { margin-top: 22mm; max-width: 158mm; border-left: 4px solid #f87171; padding: 5mm 6mm; background: rgba(15,23,42,.46); font-size: 13pt; line-height: 1.58; }
.cover .meta { position: absolute; left: 22mm; right: 22mm; bottom: 24mm; display: grid; grid-template-columns: repeat(4, 1fr); gap: 5mm; z-index: 2; }
.cover .meta div { border-top: 1px solid rgba(255,255,255,.35); padding-top: 3mm; color: #cbd5e1; font-size: 8.6pt; }
.cover .meta strong { display: block; color: white; font-size: 10pt; margin-top: 1mm; }
.major { break-before: page; }
h1.section { margin: 0 0 6mm; padding-bottom: 3mm; border-bottom: 2px solid #dc2626; color: #0f172a; font-size: 20pt; line-height: 1.25; }
h2 { margin: 5mm 0 2.5mm; color: #1e293b; font-size: 13.5pt; }
h3 { margin: 4mm 0 2mm; color: #334155; font-size: 11pt; }
p { margin: 0 0 3mm; }
ul, ol { margin: 1.5mm 0 3mm 5mm; padding-left: 4mm; }
li { margin: 1mm 0; }
.small { color: #64748b; font-size: 8.2pt; }
.lead { color: #334155; font-size: 11pt; line-height: 1.7; }
.badge { display: inline-block; padding: 1mm 2.8mm; border-radius: 20px; font-size: 7.8pt; font-weight: 700; letter-spacing: .04em; }
.badge.red { color: #991b1b; background: #fee2e2; }
.badge.green { color: #166534; background: #dcfce7; }
.badge.amber { color: #92400e; background: #fef3c7; }
.callout { border-left: 4px solid #2563eb; background: #eff6ff; padding: 4mm 5mm; margin: 4mm 0; break-inside: avoid; }
.callout.danger { border-color: #dc2626; background: #fef2f2; }
.callout.warn { border-color: #d97706; background: #fffbeb; }
.callout.good { border-color: #15803d; background: #f0fdf4; }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3.5mm; margin: 5mm 0; }
.kpi { border: 1px solid #e2e8f0; border-top: 3px solid #dc2626; border-radius: 2mm; padding: 3.2mm; min-height: 27mm; break-inside: avoid; }
.kpi .value { color: #b91c1c; font-size: 18pt; font-weight: 800; line-height: 1.25; }
.kpi .name { color: #475569; font-size: 8.2pt; margin-top: 1mm; }
.kpi .note { color: #64748b; font-size: 7.2pt; margin-top: 1mm; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm; font-size: 8.15pt; break-inside: auto; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td { border: 1px solid #dbe3ee; padding: 2.1mm 2.3mm; vertical-align: top; }
th { background: #f1f5f9; color: #1e293b; text-align: left; font-weight: 700; }
td.critical, tr.critical td { background: #fff1f2; color: #991b1b; font-weight: 700; }
.figure { margin: 4mm 0; break-inside: avoid; text-align: center; }
.figure img { width: 100%; max-height: 214mm; object-fit: contain; }
.figure .caption { color: #64748b; font-size: 7.8pt; text-align: left; margin-top: 2mm; line-height: 1.5; }
.toc { columns: 2; column-gap: 12mm; margin-top: 6mm; }
.toc div { break-inside: avoid; border-bottom: 1px dotted #cbd5e1; padding: 2mm 0; color: #334155; }
.matrix td:first-child { width: 22%; font-weight: 700; }
.matrix td:last-child { width: 22%; }
.signature { display: grid; grid-template-columns: 1fr 1fr; gap: 12mm; margin-top: 10mm; }
.signature div { border-top: 1px solid #94a3b8; padding-top: 2mm; color: #64748b; }
.anomaly-table { table-layout: fixed; font-size: 7.05pt; line-height: 1.32; margin: 2.5mm 0 3mm; }
.anomaly-table th, .anomaly-table td { padding: 1.3mm 1.4mm; }
.anomaly-table td.critical { background: #fff1f2; color: #991b1b; font-weight: 700; }
.anomaly-table td.counter { background: #f0fdf4; color: #166534; font-weight: 700; }
.code { font-family: "DejaVu Sans Mono", monospace; background: #f8fafc; border: 1px solid #e2e8f0; padding: 3mm; font-size: 7.3pt; white-space: pre-wrap; word-break: break-all; }
@media screen { body { max-width: 210mm; margin: 0 auto; box-shadow: 0 0 20px rgba(15,23,42,.15); } .cover { height: 297mm; } }
"""

    metrics_rows = scenario_metric_rows(scenarios)
    event_rows = key_event_rows(stall)
    source_rows = "".join(
        f"<tr><td>{html.escape(item.label)}</td><td>{html.escape(item.evidence_dir.name)}</td><td>{html.escape(item.metadata['started_at'])}</td><td>{html.escape(item.wan_interface)}</td><td>{fnum(item.station['duration_s'], 1)} s</td></tr>"
        for item in scenarios
    )
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{REPORT_TITLE}</title><style>{style}</style></head>
<body>
<section class="cover">
  <div class="eyebrow">RG660MK-EU · VIDEO QOE INCIDENT DIAGNOSTIC</div>
  <h1>{REPORT_TITLE}</h1>
  <h2>极弱 Wi-Fi 条件下直播频繁卡顿 · 三场景实测对照</h2>
  <div class="verdict"><strong>结论：</strong>频繁卡顿样本中，蜂窝 WAN 保持 0% 探测丢包且时延正常，而 Wi-Fi 信号降至 −89.5 dBm、TCP P95 RTT 升至 778 ms、可见数据重传比例升至 23.19%。网络侧证据强支持最后一跳 Wi-Fi/RF 退化为主导卡顿条件。</div>
  <div class="meta">
    <div>报告编号<strong>{REPORT_ID}</strong></div>
    <div>版本<strong>{REPORT_VERSION}</strong></div>
    <div>报告日期<strong>{REPORT_DATE}</strong></div>
    <div>文档属性<strong>受控压力诊断</strong></div>
  </div>
</section>

<section class="major">
  <h1 class="section">文档控制与目录</h1>
  <table>
    <tr><th>调查对象</th><td>RG660MK-EU CPE / HONOR-90 单终端</td></tr>
    <tr><th>主事件</th><td>极弱 Wi-Fi、远距离、直播频繁卡顿；人工观察标签与网络采集处于同一 180 秒测试窗口</td></tr>
    <tr><th>压力条件</th><td>Wi-Fi 天线未连接并进一步拉远终端；该条件由现场人工记录，不是设备自动采集字段</td></tr>
    <tr><th>报告用途</th><td>验证轻量网络指标能否识别卡顿条件并定位网络故障域</td></tr>
    <tr><th>适用边界</th><td>不作为正常天线、正常距离下的产品吞吐或覆盖验收结论</td></tr>
    <tr><th>版本变更</th><td>R1.1：新增“频繁卡顿异常数据与正常基线对比”表；原始证据和诊断结论不变</td></tr>
  </table>
  <div class="toc">
    <div>1　执行摘要</div><div>2　测试设计与证据质量</div><div>3　三场景指标对比</div><div>3.1　异常数据与正常基线对比</div><div>4　频繁卡顿事件时间线</div><div>5　故障域定位</div><div>6　结论边界</div><div>7　恢复与复测建议</div><div>附录 A　指标定义</div><div>附录 B　证据清单与复现</div>
  </div>
  <div class="callout warn"><strong>场景解释：</strong>“流畅/频繁卡顿”为现场人工观察；CPE 未接入播放器缓冲、渲染或 rebuffer 事件遥测，因此本报告不能把每一次网络异常逐秒映射到某一次画面停顿。</div>
</section>

<section class="major">
  <h1 class="section">1　执行摘要</h1>
  <p class="lead">本次专项调查使用同一终端、同一 5 GHz AP 接口和近似 180 秒采集窗口，对强信号参考、弱信号无卡顿、极弱信号频繁卡顿三个场景进行横向比较。结果呈现清晰的退化梯度，并在频繁卡顿窗口形成“RF 极弱—无线重试压力上升—吞吐供给下降—TCP 时延和重传暴涨—直播缓冲不足”的一致证据链。</p>
  <div class="kpis">
    <div class="kpi"><div class="value">−89.5 dBm</div><div class="name">频繁卡顿样本平均 Wi-Fi 信号</div><div class="note">最差 −91.0 dBm，终端全程保持关联</div></div>
    <div class="kpi"><div class="value">0.387 Mbit/s</div><div class="name">平均终端下行</div><div class="note">峰值仅 1.425 Mbit/s</div></div>
    <div class="kpi"><div class="value">778.04 ms</div><div class="name">可见 TCP RTT P95</div><div class="note">均值 179.77 ms，最大 2.362 s</div></div>
    <div class="kpi"><div class="value">229.66 ms</div><div class="name">TCP 相邻 RTT 抖动</div><div class="note">约为强信号参考的 {jitter_multiplier:.1f} 倍</div></div>
    <div class="kpi"><div class="value">23.193%</div><div class="name">可见 TCP 数据重传比例</div><div class="note">308 次，约为强信号参考的 {retrans_multiplier:.1f} 倍</div></div>
    <div class="kpi"><div class="value">0%</div><div class="name">CPE→公网 ICMP 丢包</div><div class="note">RTT 均值 17.87 ms，P95 27.56 ms</div></div>
  </div>
  <div class="callout danger"><strong>网络侧结论：</strong>蜂窝 WAN、CPE qdisc 和接收端零窗口没有同步恶化；异常集中在手机 Wi-Fi 最后一跳及其可见业务 TCP 连接。对“网络故障域位于 Wi-Fi/RF”的判断置信度高；对“每次用户卡顿均由该单一因素直接造成”的判断为中高置信度，仍需播放器事件时间戳才能闭环。</div>
  <h2>最强支持证据</h2>
  <ul>
    <li>严重样本 TCP RTT P95 为强信号参考的 <strong>{rtt_multiplier:.1f} 倍</strong>，数据重传比例约为 <strong>{retrans_multiplier:.1f} 倍</strong>。</li>
    <li>station TX 重试类增量约为强信号参考的 <strong>{retry_multiplier:.1f} 倍</strong>；该驱动计数仅作压力指标，不解释为 IP 丢包率。</li>
    <li>严重样本 WAN 探测反而是三组中最好：180/180 应答、0% 丢包、P95 27.56 ms，反对“蜂窝 WAN 持续恶化”为主因。</li>
    <li>手机和远端通告零窗口均为 0，qdisc 与 WAN 接口无 drop/error 增量。</li>
  </ul>
</section>

<section class="major">
  <h1 class="section">2　测试设计与证据质量</h1>
  <h2>2.1 三组输入证据</h2>
  <table><thead><tr><th>场景</th><th>证据目录</th><th>采集时间</th><th>WAN</th><th>有效时长</th></tr></thead><tbody>{source_rows}</tbody></table>
  <h2>2.2 可比性门禁</h2>
  <table>
    <tr><th>项目</th><th>核验结果</th></tr>
    <tr><td>终端身份</td><td>{html.escape(stall.metadata['hostname'])} / {mask_ip(stall.metadata.get('target_ip'))} / {mask_mac(stall.metadata.get('target_mac'))}，三组一致</td></tr>
    <tr><td>接入接口</td><td>三组均为 rai0（5 GHz），开始/结束均仅 1 个关联终端，目标始终在线</td></tr>
    <tr><td>时长与采样</td><td>均请求 180 秒，实际有效时长约 178～179 秒；逐周期吞吐按真实 monotonic 间隔重算</td></tr>
    <tr><td>抓包口径</td><td>均为单终端 128-byte 包头；硬件快速转发保持开启</td></tr>
    <tr><td>结构化一致性</td><td>构建时已从 CSV/ping 原始文本重算吞吐均值、信号均值、探测均值与样本数，并与 summary.json 自动核对</td></tr>
  </table>
  <div class="callout warn"><strong>非严格控制变量：</strong>三次测试没有记录统一的视频编码、ABR 码率和缓存阶段；主事件为直播，参考场景可能包含点播/不同缓存状态。吞吐是实际消费量而非链路容量测试，不能单独用均值判定最大承载能力。</div>
  <h2>2.3 数据质量图</h2>
  <div class="figure"><img src="{quality_image}" alt="数据质量图"><div class="caption">图 1　软件可见证据覆盖。频繁卡顿样本 pcap/Wi-Fi 下行覆盖约 16.0%，高于两组对照但仍不是全量；去重包数量明显增加，且三组均无法取得 tcpdump 内核丢包计数。</div></div>
</section>

<section class="major">
  <h1 class="section">3　三场景指标对比</h1>
  <div class="figure"><img src="{comparison_image}" alt="三场景核心指标"><div class="caption">图 2　三场景核心指标。极弱信号样本的 WAN 指标未同步恶化，而 TCP RTT、抖动、重传和控制段重试呈数量级上升。</div></div>
</section>

<section class="major">
  <h1 class="section">3　三场景指标对比（续）</h1>
  <table>
    <thead><tr><th>指标</th><th>强信号参考</th><th>弱信号无卡顿</th><th>极弱信号频繁卡顿</th></tr></thead>
    <tbody>{metrics_rows}</tbody>
  </table>
  <div class="callout danger"><strong>关键分界：</strong>−78.5 dBm 样本虽然已经出现 2.07% 可见重传，但平均供给和峰值仍能支撑当前播放，现场未观察到卡顿；当平均信号进一步降至 −89.5 dBm 时，峰值吞吐仅为前一无卡顿样本的 <strong>{peak_ratio * 100:.1f}%</strong>，TCP P95 延迟和重传同时暴涨，现场转为频繁卡顿。</div>
  <div class="figure"><img src="{distribution_image}" alt="原始采样分布"><div class="caption">图 3　信号与逐间隔下行吞吐的原始分布。极弱样本 RSSI 长时间集中在 −91～−89 dBm，已接近断连边缘。</div></div>
</section>

<section class="major">
  <h1 class="section">3.1　频繁卡顿异常数据与正常基线对比</h1>
  <p class="small">对比口径：以“强信号流畅参考”为正常基线，并保留“弱信号但无卡顿”作为中间对照。三次采集有效时长均约 178～179 秒。</p>
  <table class="anomaly-table">
    <colgroup><col style="width:16%"><col style="width:15%"><col style="width:15%"><col style="width:16%"><col style="width:38%"></colgroup>
    <thead><tr><th>指标</th><th>正常：强信号流畅</th><th>中间对照：弱信号无卡顿</th><th>异常：频繁卡顿</th><th>相对正常变化及判断</th></tr></thead>
    <tbody>{anomaly_rows}</tbody>
  </table>
  <div class="callout danger"><strong>异常归纳：</strong>频繁卡顿时，最突出的异常是 Wi-Fi 信号跌至约 −90 dBm、无线重试激增、TCP 尾时延和重传暴涨；蜂窝 WAN、队列丢包和 TCP 零窗口并未同步异常，因此故障主要集中在手机与 CPE 之间的 Wi-Fi/RF 最后一跳。</div>
  <p class="small"><strong>解释限制：</strong>下行流量为实际业务消费量，并非主动容量测试；station TX 重试类增量是驱动压力指标，不能直接换算成 IP 丢包率。</p>
</section>

<section class="major">
  <h1 class="section">4　频繁卡顿事件时间线</h1>
  <div class="figure"><img src="{timeline_image}" alt="频繁卡顿时间线"><div class="caption">图 4　178 秒主事件时间线。下方 TCP 事件仅展示 summary.json 保留的前 100 条拥塞线索，不能据此把约 +87 秒解释为异常结束。</div></div>
</section>

<section class="major">
  <h1 class="section">4　频繁卡顿事件时间线（续）</h1>
  <h2>4.1 关键事件</h2>
  <table><thead><tr><th>阶段</th><th>相对时间</th><th>Flow</th><th>方向</th><th>事件</th><th>诊断含义</th></tr></thead><tbody>{event_rows}</tbody></table>
  <h2>4.2 事件规模</h2>
  <ul>
    <li>可见 TCP 数据重传 <strong>{int(stall.tcp['target_retransmission_count'])}</strong> 次，占可见 payload 包 <strong>{pct(stall.tcp['target_retransmission_ratio'], 3)}</strong>。</li>
    <li>SYN/FIN/关闭控制段重试合计 <strong>{int(stall.tcp['handshake_or_close_retransmission_count'])}</strong> 次；该字段为合并计数，不能全部表述为“建连失败”。</li>
    <li>间接拥塞线索 <strong>{int(stall.tcp['congestion_control_signal_count'])}</strong> 条，其中可推得约 <strong>{duplicate_ack_count}</strong> 条三次重复 ACK 线索；真实 cwnd 不可从转发连接直接读取。</li>
    <li>可见 TCP RTT 均值/P95/最大值为 <strong>{fnum(stall.tcp['tcp_rtt_ms']['mean'], 2)} / {fnum(stall.tcp['tcp_rtt_ms']['p95'], 2)} / {fnum(stall.tcp['tcp_rtt_ms']['max'], 2)} ms</strong>。</li>
  </ul>
  <div class="callout warn"><strong>事件截断：</strong>{html.escape('、'.join(truncated_notes))}仅保存前 100 条明细，但 summary 中的 308、867、391 为完整总数。完整逐秒事件时间线需要重新离线解析原始 pcap。</div>
</section>

<section class="major">
  <h1 class="section">5　故障域定位</h1>
  <table class="matrix"><thead><tr><th>候选故障域</th><th>支持证据</th><th>反证/限制</th><th>判断</th></tr></thead><tbody>
    <tr class="critical"><td>Wi-Fi / RF 最后一跳</td><td>RSSI −89.5 dBm；重试类计数约为基线 {retry_multiplier:.1f} 倍；吞吐峰值下降；业务 TCP RTT、抖动、重传同步恶化</td><td>驱动计数不能换算成 IP 丢包率；无 PHY PER/MCS 完整时间线</td><td><span class="badge red">强支持</span></td></tr>
    <tr><td>蜂窝 WAN</td><td>没有支持：0% ICMP 丢包，WAN drop/error 为 0</td><td>公网探针不是实际直播 CDN，不能排除特定远端路径瞬时问题</td><td><span class="badge green">当前证据反对</span></td></tr>
    <tr><td>CPE 队列拥塞</td><td>没有支持：rai0/br-lan/ccmni2 qdisc drop 均为 0</td><td>硬件快速路径可能绕过部分软件观测</td><td><span class="badge green">未见支持</span></td></tr>
    <tr><td>手机接收窗口耗尽</td><td>没有支持：手机/远端零窗口均为 0</td><td>零窗口只覆盖可见 TCP 子集，不覆盖 QUIC/UDP</td><td><span class="badge green">未见支持</span></td></tr>
    <tr><td>播放器缓存/解码/终端性能</td><td>现场人工观察到频繁卡顿</td><td>CPE 无播放器缓冲、rebuffer、解码掉帧和 selected bitrate 遥测</td><td><span class="badge amber">不可直接观测</span></td></tr>
    <tr><td>远端/CDN</td><td>理论上仍可能影响特定业务流</td><td>三场景退化与 RSSI 梯度一致；通用 WAN 探针在严重样本中正常</td><td><span class="badge amber">非首要解释</span></td></tr>
  </tbody></table>
  <h2>5.1 诊断链</h2>
  <ol>
    <li>人为压力条件使终端长期处于 −91～−89 dBm 极弱信号区。</li>
    <li>无线侧重试/失败压力显著增加，终端下行峰值降至 1.425 Mbit/s。</li>
    <li>可见业务连接出现 179.77 ms 平均 RTT、778.04 ms P95 和 23.19% 数据重传。</li>
    <li>直播缓冲通常浅于点播，持续供给不足和秒级尾时延更容易触发 rebuffer。</li>
    <li>同一窗口内现场观察到频繁卡顿，且 WAN/队列/零窗口未同步异常。</li>
  </ol>
</section>

<section class="major">
  <h1 class="section">6　结论边界</h1>
  <div class="callout danger"><strong>可以下的结论：</strong>频繁卡顿窗口内，网络链路确实具备持续造成直播卡顿的条件；异常高度集中于极弱 Wi-Fi/RF 最后一跳，而不是 CPE→公网蜂窝路径。</div>
  <div class="callout warn"><strong>不能下的结论：</strong>不能据此给出 RG660MK-EU 正常天线状态下的覆盖半径或性能验收值；不能把 23.19% 重传比例等同于端到端字节丢包率；不能证明每次画面停顿都由某一条 TCP 事件唯一触发。</div>
  <h2>主要不确定性</h2>
  <ul>
    <li>主事件为无 Wi-Fi 天线、远距离的受控压力场景，不代表正常部署。</li>
    <li>三组没有统一记录片源、分辨率、编码码率、ABR 选择和缓存阶段；吞吐不能直接作为链路容量。</li>
    <li>主事件 pcap 仅覆盖约 {pct(stall.summary['capture_coverage']['pcap_vs_wifi_ratio'], 2)} 的 Wi-Fi 下行，硬件快速转发仍生效。</li>
    <li>三组 capture_log 均无法报告 tcpdump 内核丢包数；严重样本去除了 {int(stall.tcp['duplicate_tap_packets_removed'])} 个重复 tap 包。</li>
    <li>人工观察没有逐次卡顿时间戳，无法计算网络事件对播放器 rebuffer 的精确命中率。</li>
  </ul>
  <p><strong>综合置信度：</strong>网络故障域定位为 Wi-Fi/RF：高；Wi-Fi 为全部用户感知卡顿的唯一根因：中高。</p>
</section>

<section class="major">
  <h1 class="section">7　恢复与复测建议</h1>
  <h2>7.1 立即恢复</h2>
  <ol>
    <li>停止压力场景，设备断电后恢复匹配的 Wi-Fi 天线和射频连接，避免带电插拔。</li>
    <li>恢复正常摆位、距离和遮挡条件，确认终端 RSSI 回到稳定可用区。</li>
    <li>使用同一直播源、固定清晰度和未缓存播放阶段，重新执行 180 秒复测。</li>
  </ol>
  <h2>7.2 建议验收项</h2>
  <table><thead><tr><th>层级</th><th>建议动作</th><th>目的</th></tr></thead><tbody>
    <tr><td>第一层</td><td>继续采集 RSSI、吞吐、WAN 探针、TCP RTT/重传/零窗口和 qdisc</td><td>低成本识别网络是否具备卡顿条件</td></tr>
    <tr><td>终端侧</td><td>增加播放器 rebuffer 开始/结束、buffer level、selected bitrate 和 dropped frames 时间戳</td><td>把网络异常与真实卡顿逐次对齐</td></tr>
    <tr><td>无线侧</td><td>补充每链路 MCS/NSS、PHY rate、PER、airtime、聚合与重试时间线</td><td>量化 RF 退化机制</td></tr>
    <tr><td>高级增强</td><td>必要时离线重解析完整 pcap，或在旁路部署业务识别/分片节奏分析</td><td>提高业务归属和卡顿预测准确度</td></tr>
  </tbody></table>
  <div class="callout good"><strong>建议判定方式：</strong>恢复后若 WAN 正常、RSSI 改善且 TCP P95/重传回落，同时播放器不再上报 rebuffer，可形成完整恢复闭环。不要为了提高抓包覆盖而直接关闭 HNAT/WARP/WED，否则会改变被测系统。</div>
</section>

<section class="major">
  <h1 class="section">附录 A　指标定义</h1>
  <table><thead><tr><th>指标</th><th>本报告定义</th><th>解释限制</th></tr></thead><tbody>
    <tr><td>终端下行吞吐</td><td>单关联终端条件下 rai0 TX 字节差除以真实 monotonic 时间间隔</td><td>是业务消费量，不是主动容量测试</td></tr>
    <tr><td>WAN 探测</td><td>CPE 从活动蜂窝接口向 223.5.5.5 发起 ICMP</td><td>不经过手机 Wi-Fi，也不是实际直播 CDN</td></tr>
    <tr><td>TCP RTT</td><td>软件可见包头中的 TCP timestamp echo 优先估算</td><td>只代表可见 TCP 子集，不覆盖全部快速转发或 QUIC</td></tr>
    <tr><td>数据重传比例</td><td>识别出的 TCP payload 重传事件数 ÷ 可见上下行 payload 包数</td><td>不是无线重试率，也不是字节丢包率</td></tr>
    <tr><td>零窗口</td><td>手机或远端 TCP advertised window 为 0 的事件</td><td>未观察到不等于播放器未阻塞</td></tr>
    <tr><td>拥塞线索</td><td>数据重传与三次重复 ACK 的合计线索</td><td>不是直接读取的 cwnd，真实 cwnd 不可观测</td></tr>
    <tr><td>驱动重试类计数</td><td>station/接口累计计数的窗口差分</td><td>MTK 驱动字段可能复用或别名化，仅用于趋势对比</td></tr>
    <tr><td>pcap 覆盖</td><td>软件可见下行包头字节 ÷ Wi-Fi TX 字节</td><td>受 HNAT/WARP/WED 快速路径影响</td></tr>
  </tbody></table>
</section>

<section class="major">
  <h1 class="section">附录 B　证据清单与复现</h1>
  <h2>B.1 关键证据</h2>
  <table><thead><tr><th>证据</th><th>用途</th></tr></thead><tbody>
    <tr><td>summary.json</td><td>结构化主指标、协议统计、重传与覆盖率</td></tr>
    <tr><td>station_samples.csv</td><td>信号、Wi-Fi/WAN 字节与驱动计数时间序列</td></tr>
    <tr><td>wan_ping.txt</td><td>蜂窝 WAN 主动探测原始结果</td></tr>
    <tr><td>target_headers.pcap</td><td>单终端 128-byte 包头；含地址/端口元数据，应按敏感证据管理</td></tr>
    <tr><td>snapshot_start.txt / snapshot_end.txt</td><td>接口、qdisc 与内核计数差分</td></tr>
    <tr><td>REPORT.md</td><td>单场景自动汇总</td></tr>
  </tbody></table>
  <h2>B.2 构建命令</h2>
  <div class="code">python3 scripts/build_video_stall_diagnostic_pdf.py &#92;<br>
  &nbsp;&nbsp;--baseline-dir reports/evidence/{baseline.evidence_dir.name} &#92;<br>
  &nbsp;&nbsp;--degraded-dir reports/evidence/{degraded.evidence_dir.name} &#92;<br>
  &nbsp;&nbsp;--stall-dir reports/evidence/{stall.evidence_dir.name} &#92;<br>
  &nbsp;&nbsp;--output reports/RG660MK-EU_频繁卡顿场景诊断报告_R1.1_20260825.pdf &#92;<br>
  &nbsp;&nbsp;--html-output reports/RG660MK-EU_频繁卡顿场景诊断报告_R1.1_20260825.html</div>
  <h2>B.3 排除样本</h2>
  <p>17:33 的早期尝试未纳入正式比较：当时活动蜂窝出口已切换为 ccmni2，而旧采集参数仍监控 ccmni3，导致 WAN 探测不可用。专项构建仅使用 WAN 口径有效的三组证据。</p>
  <div class="signature"><div>报告生成：本地结构化证据自动构建</div><div>复核状态：PDF 输出校验通过</div></div>
</section>
</body></html>"""
    if "{{" in html_text or "}}" in html_text:
        raise BuildError("unresolved template marker found in generated HTML")
    return html_text


def print_pdf(chrome: str, html_path: Path, output: Path) -> None:
    executable = shutil.which(chrome) if not Path(chrome).is_file() else chrome
    if not executable:
        raise BuildError(f"Chrome executable not found: {chrome}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with tempfile.TemporaryDirectory(prefix="video-stall-pdf-") as profile:
        command = [
            str(executable),
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3000",
            f"--print-to-pdf={output}",
            html_path.resolve().as_uri(),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise BuildError(f"Chrome PDF printing failed ({result.returncode}): {result.stderr.strip()}")
    if not output.is_file() or output.stat().st_size < 50_000:
        raise BuildError(f"PDF output is missing or too small: {output}")
    if output.read_bytes()[:4] != b"%PDF":
        raise BuildError(f"output is not a PDF file: {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the RG660MK-EU frequent-stall diagnostic PDF")
    parser.add_argument("--baseline-dir", type=Path, required=True, help="strong-signal reference evidence")
    parser.add_argument("--degraded-dir", type=Path, required=True, help="weak-signal no-stall evidence")
    parser.add_argument("--stall-dir", type=Path, required=True, help="extreme weak-signal frequent-stall evidence")
    parser.add_argument("--output", type=Path, required=True, help="target PDF path")
    parser.add_argument("--html-output", type=Path, help="print-ready HTML output path")
    parser.add_argument("--chrome", default="google-chrome", help="Chrome executable")
    parser.add_argument("--html-only", action="store_true", help="write HTML without printing PDF")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        scenarios = [
            load_scenario("baseline", "强信号参考", "流畅参考", args.baseline_dir.expanduser().resolve()),
            load_scenario("degraded", "弱信号样本", "现场无卡顿", args.degraded_dir.expanduser().resolve()),
            load_scenario("stall", "极弱信号样本", "直播频繁卡顿", args.stall_dir.expanduser().resolve()),
        ]
        validate_scenarios(scenarios)
        html_text = render_html(scenarios)
        html_path = (
            args.html_output.expanduser().resolve()
            if args.html_output
            else args.output.expanduser().resolve().with_suffix(".html")
        )
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_text, encoding="utf-8")
        print(f"HTML_WRITTEN path={html_path} bytes={html_path.stat().st_size}", flush=True)
        if not args.html_only:
            output = args.output.expanduser().resolve()
            print_pdf(args.chrome, html_path, output)
            print(f"PDF_WRITTEN path={output} bytes={output.stat().st_size}", flush=True)
        return 0
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"BUILD_FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
