# Infra Spec

## AKS 클러스터

| 항목              | 값                         |
| ----------------- | -------------------------- |
| 클러스터 이름     | `aks-localdns-test`        |
| 리소스 그룹       | `rg-localdns-test`         |
| 리전              | `swedencentral`            |
| Kubernetes 버전   | `1.33.7`                   |
| 네트워크 플러그인 | Azure CNI Overlay + Cilium |
| Pod CIDR          | `10.244.0.0/16`            |
| Service CIDR      | `10.0.0.0/16`              |
| DNS Service IP    | `10.0.0.10`                |

## 노드 풀

| 노드 풀    | 역할   | VM 크기             | 노드 수 | Max Pods |
| ---------- | ------ | ------------------- | ------- | -------- |
| `system`   | System | `Standard_D16as_v6` | 2       | 기본값   |
| `userpool` | User   | `Standard_D16as_v6` | 5       | 60       |

\*userpool의 노드 수는 테스트 중에 변경될 수 있습니다.

## LocalDNS 설정 값

- **모드**: `Required` (LocalDNS를 반드시 사용)
- **DNS IP**: `169.254.10.10` 또는 `169.254.10.11` (노드에 따라 resolv.conf에 설정됨)

### DNS 오버라이드 규칙

| 도메인                    | 프로토콜  | 포워딩 대상    | 캐시 TTL | Serve Stale        |
| ------------------------- | --------- | -------------- | -------- | ------------------ |
| `.` (vnetDNS)             | PreferUDP | VnetDNS        | 3600초   | Immediate (3600초) |
| `cluster.local` (vnetDNS) | ForceTCP  | ClusterCoreDNS | 3600초   | Immediate (3600초) |
| `.` (kubeDNS)             | PreferUDP | ClusterCoreDNS | 3600초   | Immediate (3600초) |
| `cluster.local` (kubeDNS) | ForceTCP  | ClusterCoreDNS | 3600초   | Immediate (3600초) |

## 파일 구성

| 파일                   | 설명                              |
| ---------------------- | --------------------------------- |
| `main.bicep`           | 리소스 그룹 생성 및 AKS 모듈 호출 |
| `aks.bicep`            | AKS 클러스터 리소스 정의          |
| `main.parameters.json` | Bicep 파라미터 파일 (기본값 사용) |
| `localdnsconfig.json`  | LocalDNS override 설정 파일       |
