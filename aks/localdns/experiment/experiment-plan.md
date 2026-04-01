# AKS LocalDNS 도입 실험 계획서

## 1. 실험 목적

이 실험은 AKS 클러스터에서 LocalDNS 도입 전후의 DNS 응답 성능 차이를 정량적으로 확인하는 것을 목적으로 한다.

단순 평균 latency만 보는 것이 아니라, 부하 수준과 클러스터 규모가 달라질 때 LocalDNS의 효과가 일관되게 유지되는지도 함께 확인한다.

| 확인 항목   | 내용                                                                                |
| ----------- | ----------------------------------------------------------------------------------- |
| 비교 대상   | Baseline(LocalDNS 비활성화) vs LocalDNS                                             |
| 비교 축     | 노드 수 5/10, Pod당 QPS 20/40/80/160                                                |
| 주요 지표   | avg, p50, p95, p99 latency / achieved QPS / query loss                              |
| 확인 포인트 | 부하 증가 시 latency 분포가 어떻게 변하는지, 노드 수 변화에 따라 효과 차이가 있는지 |

---

## 2. 실험 환경 구성

### 2.1 AKS 클러스터 스펙

```
리소스 그룹     : rg-localdns-test
클러스터 이름    : aks-localdns-test
리전            : Sweden Central (swedencentral)
Kubernetes 버전 : 1.33.7
노드 풀         :
  - system pool : Standard_D16as_v6 x 2 (시스템 워크로드 전용)
  - user pool   : Standard_D16as_v6 x 5 또는 10 (dnsperf 워크로드, 노드당 ~50 Pod)
네트워크 플러그인 : Azure CNI Overlay
데이터플레인     : Cilium
노드 OS         : Ubuntu 22.04.05 LTS
```

### 2.2 실험 매트릭스

| 변수      | 조건                          |
| --------- | ----------------------------- |
| 노드 수   | 5, 10                         |
| LocalDNS  | OFF (baseline), ON (localdns) |
| Pod당 QPS | 20, 40, 80, 160               |
| 반복 횟수 | 3회 (조건 당)                 |

> 노드 수에 따라 CoreDNS replica 수와 총 Pod 수가 변동한다.

| 노드 수 | dnsperf Pod | CoreDNS replica (예상) |
| ------- | ----------- | ---------------------- |
| 5       | 250         | 2                      |
| 10      | 500         | 3~4                    |

### 2.3 클러스터 프로비저닝 (azd + Bicep)

인프라를 `azd` (Azure Developer CLI) + Bicep 으로 관리한다.

```bash
azd init        # 환경 초기화 (최초 1회)
azd provision   # 프로비저닝

az aks get-credentials --resource-group rg-localdns-test --name aks-localdns-test
```

> Bicep 정의: `infra/main.bicep`, `infra/aks.bicep` / azd 설정: `azure.yaml`

> 이후 노드 스케일링, CoreDNS replica 확인, LocalDNS 적용은 `scripts/run-all.sh`가 Phase 전환에 맞춰 자동으로 수행한다.

---

## 3. 실험 입력 구성

실험 입력은 `run-all.sh`가 공통으로 적용하는 DNS 조회 대상과 dnsperf 쿼리 목록으로 구성한다.

### 3.1 Dummy Service 배포 (`manifests/dummy-services.yaml`)

dnsperf가 조회할 Internal 도메인이 실제로 존재하도록 Headless Service(`clusterIP: None`)를 3개 namespace에 배포한다.

| Namespace | Services                                                                                  |
| --------- | ----------------------------------------------------------------------------------------- |
| `app-a`   | api-gateway, user-service, order-service, payment-service, notification-service           |
| `app-b`   | product-catalog, inventory-service, search-service, recommendation-service, cache-service |
| `app-c`   | auth-service, logging-service, monitoring-service, config-service, messaging-service      |

> 총 15개 Headless Service → Internal FQDN 생성

### 3.2 쿼리 파일 (`manifests/dnsperf-queryfile-cm.yaml`)

dnsperf에 입력할 도메인 목록을 ConfigMap으로 모든 Pod에 마운트한다.

| 구분                            | 개수     | 예시                                                                             |
| ------------------------------- | -------- | -------------------------------------------------------------------------------- |
| Internal (클러스터 서비스 FQDN) | 17개     | `api-gateway.app-a.svc.cluster.local`, `kubernetes.default.svc.cluster.local` 등 |
| External (Azure 서비스)         | 10개     | `login.microsoftonline.com`, `management.azure.com`, `vault.azure.net` 등        |
| External (일반 도메인)          | 5개      | `google.com`, `github.com`, `amazonaws.com` 등                                   |
| **합계**                        | **32개** |                                                                                  |

---

## 4. 실험 실행 구성

### 4.1 부하 조건

Pod당 QPS를 4단계로 변경해 부하를 올리며, 각 조건은 3회 반복한다.

| QPS 조건      | Pod당 QPS (`-Q`) | 총 QPS (5노드/250 Pod) | 총 QPS (10노드/500 Pod) |
| ------------- | ---------------- | ---------------------- | ----------------------- |
| **Low**       | 20               | 5,000                  | 10,000                  |
| **Medium**    | 40               | 10,000                 | 20,000                  |
| **High**      | 80               | 20,000                 | 40,000                  |
| **Very High** | 160              | 40,000                 | 80,000                  |

| 항목              | 값                           |
| ----------------- | ---------------------------- |
| 노드              | D16as_v6 × 5 또는 10         |
| Pod 수            | 250 또는 500                 |
| Image             | `guessi/dnsperf:latest`      |
| dnsperf 고정 옵션 | `-c 10` `-S 10` `-l 60` `-v` |
| 반복 횟수         | 조건 당 3회                  |

### 4.2 실행 파일 역할

| 파일                                | 역할                                  |
| ----------------------------------- | ------------------------------------- |
| `manifests/dnsperf-job-node5.yaml`  | 5노드용 dnsperf Job 템플릿 (250 Pod)  |
| `manifests/dnsperf-job-node10.yaml` | 10노드용 dnsperf Job 템플릿 (500 Pod) |
| `scripts/run-test.sh`               | 단일 조건 실행 및 raw 로그 수집       |
| `scripts/01_aggregate_results.py`   | 조건별 run summary 생성               |
| `scripts/02_collect_summary.py`     | 전체 summary 통합                     |
| `scripts/03_generate_report.py`     | 최종 보고서 생성                      |

> `run-test.sh`는 선택한 Job 템플릿의 이름과 `-Q` 값을 실행 시점에 치환한다.

### 4.3 집계 지표와 산출물

각 조건(노드 수 × QPS × LocalDNS 유무)에 대해 3회 시행하고, 각 run을 독립적으로 집계한다.

- Latency: avg, min, max, stddev, p50, p90, p95, p99 (ms)
- 처리량: queries sent, queries completed, achieved QPS
- 손실률: queries lost (%)

| 산출물         | 경로                                                 |
| -------------- | ---------------------------------------------------- |
| run별 raw 로그 | `results/<N>nodes/qps-<Q>/<phase>/runN/raw/`         |
| run별 요약     | `results/<N>nodes/qps-<Q>/<phase>/runN/summary.json` |
| 전체 통합 결과 | `results/summary.json`                               |
| 최종 보고서    | `experiment-result.md`                               |

---

## 5. 실험 절차

실제 실행은 `scripts/run-all.sh` 기준으로 진행한다. 이 스크립트가 phase 전환, 노드 스케일링, CoreDNS 확인, LocalDNS 적용, 결과 집계를 순차 처리한다.

### 5.1 실행 명령

```bash
nohup ./scripts/run-all.sh > run-all.log 2>&1 &
```

필요하면 특정 phase부터 재시작할 수 있다.

```bash
./scripts/run-all.sh -p 2
./scripts/run-all.sh --phase 3
```

### 5.2 Phase 구성

| Phase | 조건             | 자동 수행 내용                                                                          |
| ----- | ---------------- | --------------------------------------------------------------------------------------- |
| 1     | Baseline, 5노드  | 공통 manifests 적용, CoreDNS 상태 확인, QPS 20/40/80/160 실행, run별 결과 집계          |
| 2     | Baseline, 10노드 | userpool 10노드 스케일링, CoreDNS 확인, QPS 실행, run별 결과 집계                       |
| 3     | LocalDNS, 5노드  | userpool 5노드 스케일링, LocalDNS 적용 및 `resolv.conf` 검증, QPS 실행, run별 결과 집계 |
| 4     | LocalDNS, 10노드 | userpool 10노드 스케일링, 필요 시 LocalDNS 적용, QPS 실행, run별 결과 집계              |
| 5     | 결과 통합        | `results/summary.json` 생성, `experiment-result.md` 생성                                |

### 5.3 내부 실행 흐름

1. 필요한 phase 시작 시 manifests를 적용한다.
2. 노드 수가 바뀌는 phase에서는 userpool 스케일링 후 CoreDNS replica를 확인한다.
3. LocalDNS phase 시작 시 `infra/localdnsconfig.json`으로 LocalDNS를 적용하고 `resolv.conf`를 검증한다.
4. 각 phase에서 QPS 20, 40, 80, 160을 순서대로 실행하고, 각 조건마다 `run-test.sh`와 `01_aggregate_results.py`를 호출한다.
5. 모든 phase가 끝나면 `02_collect_summary.py`, `03_generate_report.py`를 순차 실행한다.

---

## 6. 필요 리소스 정리

| 리소스          | 스펙                                  |
| --------------- | ------------------------------------- |
| AKS system pool | Standard_D16as_v6 x 2                 |
| AKS user pool   | Standard_D16as_v6 x 5 → 10 (스케일링) |

> ⚠️ 실험 완료 후 반드시 리소스 삭제  
> `azd down --purge --force`

---

## 7. 디렉토리 구조

```
aks-localdns-test/
├── azure.yaml                      # azd 프로젝트 설정
├── experiment-plan.md              # 본 실험 계획서
├── experiment-result.md            # 최종 결과 보고서
├── infra/
│   ├── main.bicep                  # Bicep 진입점 (subscription scope)
│   ├── main.parameters.json        # Bicep 파라미터
│   ├── aks.bicep                   # AKS 클러스터 리소스 정의
│   └── localdnsconfig.json         # LocalDNS 설정 파일
├── manifests/
│   ├── dummy-services.yaml         # Internal DNS 대상 서비스
│   ├── dnsperf-queryfile-cm.yaml   # 쿼리 도메인 목록 ConfigMap
│   ├── dnsperf-job-node5.yaml      # dnsperf Job (250 Pod, 5노드용)
│   └── dnsperf-job-node10.yaml     # dnsperf Job (500 Pod, 10노드용)
├── scripts/
│   ├── run-all.sh                  # 전체 실험 자동화 스크립트
│   ├── run-test.sh                 # 단일 조건 실행 스크립트
│   ├── 01_aggregate_results.py     # 결과 집계 (Python)
│   ├── 02_collect_summary.py       # 전체 summary 통합 (Python)
│   ├── 03_generate_report.py       # 리포트 생성 (Python)
│   └── requirements.txt            # Python 의존성
└── results/
    ├── 5nodes/
    │   ├── qps-20/
    │   │   ├── baseline/run1~3/
    │   │   └── localdns/run1~3/
    │   ├── qps-40/ ...
    │   ├── qps-80/ ...
    │   └── qps-160/ ...
    ├── 10nodes/
    │   ├── qps-20/ ...
    │   ├── qps-40/ ...
    │   ├── qps-80/ ...
    │   └── qps-160/ ...
    └── summary.json              # 전체 통합 결과
```

---

## 8. 참고 자료

### Azure / AKS

- [AKS LocalDNS 공식 문서](https://learn.microsoft.com/en-us/azure/aks/localdns-custom): LocalDNS 개념, prerequisites, enable/update/verify 절차
- [AKS CoreDNS autoscaling 공식 문서](https://learn.microsoft.com/en-us/azure/aks/coredns-autoscale): CoreDNS replica 산정 기준과 `coredns-autoscaler` 기본 ladder 값
- [AKS Engineering Blog — Accelerate DNS Performance with LocalDNS](https://blog.aks.azure.com/2025/08/04/accelerate-dns-performance-with-localdns): LocalDNS 도입 배경과 기대 효과

### 부하 생성 도구

- [dnsperf — DNS-OARC](https://www.dns-oarc.net/tools/dnsperf): dnsperf 개요, 배포 패키지, 기본 문서 링크
- [dnsperf README — DNS-OARC](https://codeberg.org/DNS-OARC/dnsperf/src/branch/main/README.md): 실행 옵션과 usage 참고
