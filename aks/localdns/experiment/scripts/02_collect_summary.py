#!/usr/bin/env python3
"""
전체 실험 결과를 하나의 summary.json으로 통합.

사용법:
  python3 scripts/02_collect_summary.py

results/{N}nodes/qps-*/[baseline|localdns]/run*/summary.json 을 모두 읽어서
results/summary.json 으로 출력한다.

출력 구조:
{
  "5nodes": {
    "qps-20": {
      "baseline": [ {run1 summary}, ... ],
      "localdns": [ {run1 summary}, ... ]
    },
    ...
  },
  "10nodes": { ... }
}
"""

import json
from pathlib import Path


def main():
    results_dir = Path("results")
    output = {}

    for nodes_dir in sorted(results_dir.glob("*nodes")):
        if not nodes_dir.is_dir():
            continue

        nodes_key = nodes_dir.name  # "5nodes", "10nodes"
        output[nodes_key] = {}

        for qps_dir in sorted(nodes_dir.glob("qps-*")):
            if not qps_dir.is_dir():
                continue

            qps_key = qps_dir.name
            output[nodes_key][qps_key] = {}

            for phase in ["baseline", "localdns"]:
                phase_dir = qps_dir / phase
                if not phase_dir.exists():
                    continue

                runs = []
                for run_dir in sorted(phase_dir.glob("run*")):
                    summary_file = run_dir / "summary.json"
                    if summary_file.exists():
                        with open(summary_file) as f:
                            runs.append(json.load(f))

                if runs:
                    output[nodes_key][qps_key][phase] = runs

    if not output:
        print("Error: No summary.json files found")
        return

    output_path = results_dir / "summary.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # 요약 출력
    for nodes_key, qps_data in output.items():
        for qps_key, phases in qps_data.items():
            for phase, runs in phases.items():
                run_count = len(runs)
                avg_latency = sum(r.get("latency_avg_ms", 0) for r in runs) / run_count
                print(f"  {nodes_key}/{qps_key}/{phase}: {run_count} runs, avg={avg_latency:.4f}ms")

    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
