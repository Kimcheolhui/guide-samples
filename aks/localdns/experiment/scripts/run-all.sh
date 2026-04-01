#!/bin/bash
set -euo pipefail

# =============================================================================
# AKS LocalDNS 전체 실험 자동화 스크립트
#
# 사용법:
#   nohup ./scripts/run-all.sh > run-all.log 2>&1 &
#   tail -f run-all.log
#
#   특정 phase부터 시작:
#   ./scripts/run-all.sh -p 2
#   ./scripts/run-all.sh --phase 3
#
# Phase 1: Baseline  — 5노드  (250 Pod)
# Phase 2: Baseline  — 10노드 (500 Pod)
# Phase 3: LocalDNS  — 5노드  (250 Pod)
# Phase 4: LocalDNS  — 10노드 (500 Pod)
# Phase 5: 결과 통합 및 리포트 생성
# =============================================================================

# 시작 phase 인자 처리
START_PHASE=1

while [[ $# -gt 0 ]]; do
  case $1 in
    -p|--phase)
      START_PHASE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [-p|--phase <1-5>]"
      exit 1
      ;;
  esac
done

if [[ "${START_PHASE}" -lt 1 || "${START_PHASE}" -gt 5 ]]; then
  echo "ERROR: phase must be between 1 and 5"
  exit 1
fi

# 실험 공통 설정
RESOURCE_GROUP="rg-localdns-test"
CLUSTER_NAME="aks-localdns-test"
NODEPOOL="userpool"
RUNS=3
QPS_LIST=(20 40 80 160)
QPS_INTERVAL=30

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Python venv 활성화
source "${SCRIPT_DIR}/.venv/bin/activate"

# 스크립트 전체에서 cwd를 프로젝트 루트로 고정
cd "${PROJECT_DIR}"

# 공통 로그 출력
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# 하나의 phase에서 QPS 조건(20, 40, 80, 160)들을 순차 실행하고 결과 집계
run_qps_loop() {
  local phase=$1
  local nodes=$2

  for qps in "${QPS_LIST[@]}"; do
    log "--- ${nodes}nodes / ${phase} / QPS ${qps} ---"
    "${SCRIPT_DIR}/run-test.sh" "${phase}" "${qps}" "${RUNS}" "${nodes}"
    python3 "${SCRIPT_DIR}/01_aggregate_results.py" "${qps}" "${phase}" "${nodes}"

    log "QPS 간 쿨다운 ${QPS_INTERVAL}s..."
    sleep ${QPS_INTERVAL}
  done
}

# 노드풀 크기를 조정하고, 노드 개수에 따른 CoreDNS replica 상태가 적합한지 확인
scale_nodepool() {
  local count=$1
  local current
  current=$(az aks nodepool show \
    --name ${NODEPOOL} \
    --cluster-name ${CLUSTER_NAME} \
    --resource-group ${RESOURCE_GROUP} \
    --query count -o tsv)

  if [ "${current}" -eq "${count}" ]; then
    log "노드풀 이미 ${count}노드 — 스케일링 생략"
  else
    log "노드풀 스케일링: ${NODEPOOL} ${current} → ${count}노드"
    az aks nodepool scale \
      --name ${NODEPOOL} \
      --cluster-name ${CLUSTER_NAME} \
      --resource-group ${RESOURCE_GROUP} \
      --node-count ${count}
  fi

  # userpool 노드 수 기준: 5 → CoreDNS 2개, 10 → CoreDNS 3개
  # 참고) CoreDNS replica는 클러스터 전체 노드 수 기준으로 조정된다.
  # 참고) 우측 조건 중 max값 적용: {"coresToReplicas":[[1,2],[512,3],[1024,4],[2048,5]],"nodesToReplicas":[[1,2],[8,3],[16,4],[32,5]]}
  local expected_replicas
  if [ ${count} -le 5 ]; then
    expected_replicas=2
  elif [ ${count} -le 10 ]; then
    expected_replicas=3
  else
    expected_replicas=2
  fi

  log "스케일링 완료. CoreDNS replica 확인 (기대값: ${expected_replicas})..."

  local max_wait=300
  local interval=15
  local elapsed=0
  while [ ${elapsed} -lt ${max_wait} ]; do
    local actual=$(kubectl get deploy coredns -n kube-system -o jsonpath='{.status.readyReplicas}')
    actual=${actual:-0}
    if [ "${actual}" -eq "${expected_replicas}" ]; then
      log "✅ CoreDNS replica OK: ${actual}/${expected_replicas}"
      kubectl get deploy coredns -n kube-system
      return 0
    fi
    log "CoreDNS replica: ${actual}/${expected_replicas} — ${interval}s 후 재확인..."
    sleep ${interval}
    elapsed=$((elapsed + interval))
  done

  log "❌ CoreDNS replica가 기대값에 도달하지 못함 (현재: ${actual}, 기대: ${expected_replicas})"
  kubectl get deploy coredns -n kube-system
  log "실험 중단."
  exit 1
}

# LocalDNS를 적용하고 노드 내부 resolv.conf 반영 여부를 검증
enable_localdns() {
  log "LocalDNS 활성화 중..."
  az aks nodepool update \
    --name ${NODEPOOL} \
    --cluster-name ${CLUSTER_NAME} \
    --resource-group ${RESOURCE_GROUP} \
    --localdns-config "${PROJECT_DIR}/infra/localdnsconfig.json" \
    --max-surge 0 \
    --max-unavailable 1

  log "resolv.conf 확인 (169.254.10.x 여부)..."
  local max_retries=3
  local retry_wait=300
  for attempt in $(seq 1 ${max_retries}); do
    local dns_output
    dns_output=$(kubectl run verify-dns-${attempt} --image=busybox --rm -i --restart=Never \
      --overrides='{"spec":{"nodeSelector":{"agentpool":"userpool"}}}' \
      -- cat /etc/resolv.conf 2>/dev/null) || true
    echo "${dns_output}"

    if echo "${dns_output}" | grep -qE "169\.254\.10\.(10|11)"; then
      log "✅ LocalDNS 활성화 확인 완료 ($(echo "${dns_output}" | grep -oE '169\.254\.10\.(10|11)'))"
      return 0
    fi

    if [ ${attempt} -lt ${max_retries} ]; then
      log "⚠️ 시도 ${attempt}/${max_retries}: LocalDNS 미확인. ${retry_wait}s 후 재시도..."
      sleep ${retry_wait}
    fi
  done

  log "❌ LocalDNS가 활성화되지 않음 — ${max_retries}회 시도 실패"
  log "실험 중단."
  exit 1
}

# Phase 1 시작 전 baseline 상태의 CoreDNS replica를 점검
check_coredns_initial() {
  local expected=$1
  log "Phase 1 초기 CoreDNS replica 확인 (기대값: ${expected})..."

  local actual=$(kubectl get deploy coredns -n kube-system -o jsonpath='{.status.readyReplicas}')
  actual=${actual:-0}
  if [ "${actual}" -eq "${expected}" ]; then
    log "✅ CoreDNS replica OK: ${actual}/${expected}"
    kubectl get deploy coredns -n kube-system
    return 0
  fi

  log "❌ CoreDNS replica 이상 (현재: ${actual}, 기대: ${expected})"
  kubectl get deploy coredns -n kube-system
  log "실험 중단."
  exit 1
}

# 전체 phase 실행 시작
# =============================================================================
log "=========================================="
log " AKS LocalDNS 실험 시작 (Phase ${START_PHASE}부터)"
log "=========================================="

# --- Phase 1: Baseline — 5노드 ---
if [[ ${START_PHASE} -le 1 ]]; then
log ""
log "=========================================="
log " Phase 1: Baseline — 5노드 (250 Pod)"
log "=========================================="

log "사전 준비: manifests 적용"
kubectl apply -f "${PROJECT_DIR}/manifests/dummy-services.yaml"
kubectl apply -f "${PROJECT_DIR}/manifests/dnsperf-queryfile-cm.yaml"

check_coredns_initial 2

run_qps_loop "baseline" 5
fi

# --- Phase 2: Baseline — 10노드 ---
if [[ ${START_PHASE} -le 2 ]]; then
log ""
log "=========================================="
log " Phase 2: Baseline — 10노드 (500 Pod)"
log "=========================================="

if [[ ${START_PHASE} -eq 2 ]]; then
  log "사전 준비: manifests 적용"
  kubectl apply -f "${PROJECT_DIR}/manifests/dummy-services.yaml"
  kubectl apply -f "${PROJECT_DIR}/manifests/dnsperf-queryfile-cm.yaml"
fi

scale_nodepool 10
run_qps_loop "baseline" 10
fi

# --- Phase 3: LocalDNS — 5노드 ---
if [[ ${START_PHASE} -le 3 ]]; then
log ""
log "=========================================="
log " Phase 3: LocalDNS — 5노드 (250 Pod)"
log "=========================================="

if [[ ${START_PHASE} -eq 3 ]]; then
  log "사전 준비: manifests 적용"
  kubectl apply -f "${PROJECT_DIR}/manifests/dummy-services.yaml"
  kubectl apply -f "${PROJECT_DIR}/manifests/dnsperf-queryfile-cm.yaml"
fi

scale_nodepool 5
enable_localdns
run_qps_loop "localdns" 5
fi

# --- Phase 4: LocalDNS — 10노드 ---
if [[ ${START_PHASE} -le 4 ]]; then
log ""
log "=========================================="
log " Phase 4: LocalDNS — 10노드 (500 Pod)"
log "=========================================="

if [[ ${START_PHASE} -eq 4 ]]; then
  log "사전 준비: manifests 적용"
  kubectl apply -f "${PROJECT_DIR}/manifests/dummy-services.yaml"
  kubectl apply -f "${PROJECT_DIR}/manifests/dnsperf-queryfile-cm.yaml"
  enable_localdns
fi

scale_nodepool 10
run_qps_loop "localdns" 10
fi

# --- Phase 5: 결과 통합 및 리포트 ---
if [[ ${START_PHASE} -le 5 ]]; then
log ""
log "=========================================="
log " Phase 5: 결과 통합 및 리포트 생성"
log "=========================================="

cd "${PROJECT_DIR}"
python3 "${SCRIPT_DIR}/02_collect_summary.py"
python3 "${SCRIPT_DIR}/03_generate_report.py"
fi

log ""
log "=========================================="
log " 전체 실험 완료"
log "=========================================="
