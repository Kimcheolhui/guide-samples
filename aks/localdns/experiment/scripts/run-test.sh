#!/bin/bash
set -euo pipefail

# =============================================================================
# AKS LocalDNS 실험 단일 조건 실행 스크립트
#
# 하나의 실험 조건(phase, QPS, runs, nodes)에 대해서 dnsperf Job을 생성하고,
# 완료까지 대기한 뒤 pod 로그를 results 디렉터리에 수집합니다.
#
# 사용법: ./scripts/run-test.sh <phase> <qps> [runs] [nodes]
#   예: ./scripts/run-test.sh baseline 40 3 5
#       ./scripts/run-test.sh localdns 80 3 10
# =============================================================================

PHASE=${1:?Usage: $0 <baseline|localdns> <qps> [runs=3] [nodes=5]}
QPS=${2:?Usage: $0 <baseline|localdns> <qps> [runs=3] [nodes=5]}
RUNS=${3:-3}
NODES=${4:-5}
NAMESPACE="dnsperf"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# 입력 인자와 실행 대상 매니페스트 확인
MANIFEST="${PROJECT_DIR}/manifests/dnsperf-job-node${NODES}.yaml"
if [ ! -f "${MANIFEST}" ]; then
  echo "Error: manifest not found: ${MANIFEST}"
  exit 1
fi

echo "=== Config: phase=${PHASE}, qps=${QPS}, runs=${RUNS}, nodes=${NODES} ==="
echo "    Manifest: ${MANIFEST}"

# 지정한 횟수만큼 동일 조건 실험 반복
for i in $(seq 1 ${RUNS}); do
  JOB_NAME="dnsperf-${PHASE}-q${QPS}-run${i}"
  OUTPUT_DIR="${PROJECT_DIR}/results/${NODES}nodes/qps-${QPS}/${PHASE}/run${i}"
  rm -rf "${OUTPUT_DIR}/raw"
  mkdir -p "${OUTPUT_DIR}/raw"

  echo ""
  echo "========================================"
  echo " [Run ${i}/${RUNS}] ${JOB_NAME} (QPS=${QPS}, ${NODES} nodes)"
  echo "========================================"

  # 이번 run에 맞는 Job 생성
  sed -e "s/name: dnsperf-baseline-run1/name: ${JOB_NAME}/" \
      -e "s/-Q 40/-Q ${QPS}/" \
    "${MANIFEST}" \
    | kubectl apply -f -

  # Job 완료 또는 실패까지 대기
  echo "Waiting for job completion..."
  elapsed=0
  timeout=600
  while [ ${elapsed} -lt ${timeout} ]; do
    status=$(kubectl get job/${JOB_NAME} -n ${NAMESPACE} -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null || true)
    if [ "${status}" = "True" ]; then
      echo "Job ${JOB_NAME} completed."
      break
    fi
    failed=$(kubectl get job/${JOB_NAME} -n ${NAMESPACE} -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || true)
    if [ "${failed}" = "True" ]; then
      echo "Job ${JOB_NAME} failed!"
      exit 1
    fi
    sleep 10
    elapsed=$((elapsed + 10))
  done
  if [ ${elapsed} -ge ${timeout} ]; then
    echo "Timeout waiting for job ${JOB_NAME}"
    exit 1
  fi

  # 실행 결과 로그 수집
  echo "Collecting results..."
  PODS=$(kubectl get pods -n ${NAMESPACE} -l job-name=${JOB_NAME} -o jsonpath='{.items[*].metadata.name}')
  echo "${PODS}" | tr ' ' '\n' | xargs -P 20 -I {} sh -c \
    "kubectl logs -n ${NAMESPACE} {} > \"${OUTPUT_DIR}/raw/{}.log\" 2>/dev/null"

  # 다음 run을 위해 Job 정리
  kubectl delete job "${JOB_NAME}" -n ${NAMESPACE} --ignore-not-found

  echo "[Run ${i}] Done. Results in ${OUTPUT_DIR}"

  # 다음 run 전 30초 간격 확보
  if [ ${i} -lt ${RUNS} ]; then
    echo "Waiting 30s before next run..."
    sleep 30
  fi
done

echo ""
echo "=== All ${RUNS} runs complete for ${NODES}nodes/${PHASE}/qps-${QPS} ==="
