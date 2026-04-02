#!/usr/bin/env python3
"""
dnsperf 결과 집계 스크립트.

사용법:
  python3 scripts/01_aggregate_results.py <qps> <baseline|localdns>
  예: python3 scripts/01_aggregate_results.py 40 baseline

각 run 디렉토리의 Pod 로그를 파싱하여:
  1. 개별 쿼리 latency를 수집 (-v 옵션 출력)
  2. 전체 쿼리에 대한 p50, p90, p95, p99 산출
  3. 텍스트 요약 통계 파싱
  4. run별 summary.json 저장
"""

import json
import re
import sys
import numpy as np
from pathlib import Path

# 개별 쿼리 라인: "> NOERROR kubernetes.default.svc.cluster.local A 0.001170"
QUERY_RE = re.compile(r"^>\s+(\w+)\s+(\S+)\s+(\w+)\s+([\d.]+)$")

# 요약 통계 파싱용
SENT_RE = re.compile(r"Queries sent:\s+([\d]+)")
COMPLETED_RE = re.compile(r"Queries completed:\s+([\d]+)")
LOST_RE = re.compile(r"Queries lost:\s+([\d]+)")
QPS_RE = re.compile(r"Queries per second:\s+([\d.]+)")
AVG_LAT_RE = re.compile(r"Average Latency \(s\):\s+([\d.]+)\s+\(min ([\d.]+), max ([\d.]+)\)")
STDDEV_RE = re.compile(r"Latency StdDev \(s\):\s+([\d.]+)")


def parse_pod_log(filepath: Path) -> dict:
    """단일 Pod 로그 파일을 파싱."""
    query_latencies = []
    summary = {}

    content = filepath.read_text()

    for line in content.splitlines():
        line = line.strip()

        m = QUERY_RE.match(line)
        if m:
            query_latencies.append(float(m.group(4)))
            continue

        for name, regex in [
            ("sent", SENT_RE),
            ("completed", COMPLETED_RE),
            ("lost", LOST_RE),
            ("qps", QPS_RE),
        ]:
            m = regex.search(line)
            if m:
                summary[name] = float(m.group(1))

        m = AVG_LAT_RE.search(line)
        if m:
            summary["avg_s"] = float(m.group(1))
            summary["min_s"] = float(m.group(2))
            summary["max_s"] = float(m.group(3))

        m = STDDEV_RE.search(line)
        if m:
            summary["stddev_s"] = float(m.group(1))

    return {
        "pod": filepath.stem,
        "query_latencies": query_latencies,
        "summary": summary,
    }


def aggregate_run(run_dir: Path, phase: str, qps: str) -> dict | None:
    """하나의 run 디렉토리를 집계."""
    raw_dir = run_dir / "raw"
    if not raw_dir.exists():
        return None

    all_latencies = []
    pod_summaries = []
    total_sent = 0
    total_completed = 0
    total_lost = 0
    total_qps = 0.0

    for logfile in sorted(raw_dir.glob("*.log")):
        parsed = parse_pod_log(logfile)
        all_latencies.extend(parsed["query_latencies"])

        s = parsed["summary"]
        if s:
            total_sent += int(s.get("sent", 0))
            total_completed += int(s.get("completed", 0))
            total_lost += int(s.get("lost", 0))
            total_qps += s.get("qps", 0)
            pod_summaries.append({
                "pod": parsed["pod"],
                "queries": int(s.get("sent", 0)),
                "avg_ms": s.get("avg_s", 0) * 1000,
                "qps": s.get("qps", 0),
            })

    if not all_latencies:
        print(f"  {run_dir.name}: no query latency data found")
        return None

    latencies_ms = np.array(all_latencies) * 1000  # s → ms

    summary = {
        "run": run_dir.name,
        "phase": phase,
        "qps_target": int(qps),
        "pods_collected": len(pod_summaries),
        "total_queries": len(latencies_ms),
        "queries_sent": total_sent,
        "queries_completed": total_completed,
        "queries_lost": total_lost,
        "queries_lost_pct": round(total_lost / total_sent * 100, 2) if total_sent else 0,
        "qps_total": round(total_qps, 2),
        "latency_avg_ms": round(float(np.mean(latencies_ms)), 4),
        "latency_min_ms": round(float(np.min(latencies_ms)), 4),
        "latency_max_ms": round(float(np.max(latencies_ms)), 4),
        "latency_stddev_ms": round(float(np.std(latencies_ms)), 4),
        "latency_p50_ms": round(float(np.percentile(latencies_ms, 50)), 4),
        "latency_p90_ms": round(float(np.percentile(latencies_ms, 90)), 4),
        "latency_p95_ms": round(float(np.percentile(latencies_ms, 95)), 4),
        "latency_p99_ms": round(float(np.percentile(latencies_ms, 99)), 4),
    }

    with open(run_dir / "pod-results.json", "w") as f:
        json.dump(pod_summaries, f, indent=2)

    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/01_aggregate_results.py <qps> <baseline|localdns> [nodes=5]")
        sys.exit(1)

    qps = sys.argv[1]
    phase = sys.argv[2]
    nodes = sys.argv[3] if len(sys.argv) > 3 else "5"
    phase_dir = Path("results") / f"{nodes}nodes" / f"qps-{qps}" / phase

    if not phase_dir.exists():
        print(f"Error: {phase_dir} not found")
        sys.exit(1)

    print(f"=== Aggregating results for: {nodes}nodes/qps-{qps}/{phase} ===\n")

    for run_dir in sorted(phase_dir.glob("run*")):
        summary = aggregate_run(run_dir, phase, qps)
        if summary:
            summary["nodes"] = int(nodes)
            # summary.json 다시 저장 (nodes 필드 포함)
            with open(run_dir / "summary.json", "w") as f:
                json.dump(summary, f, indent=2)
            print(f"  {summary['run']}:  "
                  f"avg={summary['latency_avg_ms']}ms  "
                  f"p50={summary['latency_p50_ms']}ms  "
                  f"p95={summary['latency_p95_ms']}ms  "
                  f"p99={summary['latency_p99_ms']}ms  "
                  f"qps={summary['qps_total']}  "
                  f"lost={summary['queries_lost_pct']}%")

    print(f"\nDone. results/{nodes}nodes/qps-{qps}/{phase}/runN/summary.json")


if __name__ == "__main__":
    main()
