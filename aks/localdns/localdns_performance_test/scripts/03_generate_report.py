#!/usr/bin/env python3
"""
실험 결과 마크다운 리포트 생성.

사용법:
  python3 scripts/03_generate_report.py

results/summary.json 을 읽어서 experiment-result.md 를 생성한다.
노드 수(5, 10) × QPS(20, 40, 80, 160) × Phase(baseline, localdns) 전체 매트릭스를 포함.
"""

import json
import sys
from pathlib import Path

METRICS = [
    ("latency_avg_ms", "Avg Latency (ms)"),
    ("latency_min_ms", "Min Latency (ms)"),
    ("latency_max_ms", "Max Latency (ms)"),
    ("latency_p50_ms", "P50 Latency (ms)"),
    ("latency_p95_ms", "P95 Latency (ms)"),
    ("latency_p99_ms", "P99 Latency (ms)"),
    ("total_queries", "Total Queries"),
    ("qps_total", "Total QPS"),
]

COMPARISON_METRICS = [
    ("latency_avg_ms", "Avg Latency (ms)"),
    ("latency_min_ms", "Min Latency (ms)"),
    ("latency_max_ms", "Max Latency (ms)"),
    ("latency_p50_ms", "P50 Latency (ms)"),
    ("latency_p95_ms", "P95 Latency (ms)"),
    ("latency_p99_ms", "P99 Latency (ms)"),
]


def load_summary() -> dict:
    summary_path = Path("results/summary.json")
    if not summary_path.exists():
        print("Error: results/summary.json not found. Run 02_collect_summary.py first.")
        sys.exit(1)
    with open(summary_path) as f:
        return json.load(f)


def avg_runs(runs: list[dict], key: str) -> float:
    values = [r.get(key, 0) for r in runs]
    return sum(values) / len(values) if values else 0


def fmt(value) -> str:
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if abs(value) >= 1:
            return f"{value:.2f}"
        return f"{value:.4f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def calc_diff(b_val, l_val) -> str:
    if b_val == 0:
        return "-"
    diff_pct = (l_val - b_val) / b_val * 100
    sign = "+" if diff_pct > 0 else ""
    return f"{sign}{diff_pct:.1f}%"


def get_qps_labels(qps_data: dict) -> list[int]:
    """qps-20, qps-40, ... 에서 숫자만 추출하여 정렬."""
    labels = []
    for key in qps_data:
        num = int(key.replace("qps-", ""))
        labels.append(num)
    return sorted(labels)


def get_node_counts(data: dict) -> list[int]:
    """5nodes, 10nodes 에서 숫자만 추출하여 정렬."""
    counts = []
    for key in data:
        num = int(key.replace("nodes", ""))
        counts.append(num)
    return sorted(counts)


def pod_count(nodes: int) -> int:
    return nodes * 50


def build_phase_table(qps_data: dict, phase: str, qps_list: list[int]) -> str:
    header = "| 지표 | " + " | ".join(f"QPS {q}" for q in qps_list) + " |"
    separator = "|------|" + "|".join("------:" for _ in qps_list) + "|"

    rows = [header, separator]
    for key, label in METRICS:
        values = []
        for q in qps_list:
            runs = qps_data.get(f"qps-{q}", {}).get(phase, [])
            if runs:
                values.append(fmt(avg_runs(runs, key)))
            else:
                values.append("-")
        rows.append(f"| {label} | " + " | ".join(values) + " |")

    return "\n".join(rows)


def build_comparison_table(qps_data: dict, qps_list: list[int]) -> str:
    header = "| Pod 당 QPS | " + " | ".join(label for _, label in COMPARISON_METRICS) + " |"
    separator = "|------|" + "|".join("------:" for _ in COMPARISON_METRICS) + "|"

    rows = [header, separator]
    for q in qps_list:
        b_runs = qps_data.get(f"qps-{q}", {}).get("baseline", [])
        l_runs = qps_data.get(f"qps-{q}", {}).get("localdns", [])

        b_cells = [f"| **{q}** - Baseline |"]
        l_cells = [f"| **{q}** - LocalDNS |"]
        d_cells = [f"| **{q}** - 변화량 |"]
        for key, _ in COMPARISON_METRICS:
            if b_runs and l_runs:
                b_val = avg_runs(b_runs, key)
                l_val = avg_runs(l_runs, key)
                diff = calc_diff(b_val, l_val)
                b_cells.append(f" {fmt(b_val)} |")
                l_cells.append(f" {fmt(l_val)} |")
                d_cells.append(f" **{diff}** |")
            else:
                b_cells.append(" - |")
                l_cells.append(" - |")
                d_cells.append(" - |")
        rows.append("".join(b_cells))
        rows.append("".join(l_cells))
        rows.append("".join(d_cells))
        if q != qps_list[-1]:
            rows.append("|" + "|".join(" " for _ in range(len(COMPARISON_METRICS) + 1)) + "|")

    return "\n".join(rows)


def main():
    data = load_summary()
    node_counts = get_node_counts(data)

    lines = [
        "# AKS LocalDNS 실험 결과",
        "",
        f"> 노드 수: {', '.join(str(n) for n in node_counts)} | "
        f"QPS: 20, 40, 80, 160 | 3회 시행 평균",
        "",
    ]

    for nodes in node_counts:
        nodes_key = f"{nodes}nodes"
        qps_data = data.get(nodes_key, {})
        if not qps_data:
            continue

        qps_list = get_qps_labels(qps_data)
        pods = pod_count(nodes)

        lines.extend([
            "---",
            "",
            f"## {nodes}노드 ({pods} Pod)",
            "",
            "### Baseline (LocalDNS OFF)",
            "",
            build_phase_table(qps_data, "baseline", qps_list),
            "",
            "### LocalDNS ON",
            "",
            build_phase_table(qps_data, "localdns", qps_list),
            "",
            "### Baseline vs LocalDNS 비교",
            "",
            build_comparison_table(qps_data, qps_list),
            "",
        ])

    output = "\n".join(lines)
    output_path = Path("experiment-result.md")
    output_path.write_text(output)

    print(output)
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
