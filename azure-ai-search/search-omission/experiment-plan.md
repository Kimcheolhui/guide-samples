# Azure AI Search 데이터 누락 문제 재현 실험

## 1. 상황

Azure AI Search에서 날짜 기반 버전(e.g. `20260415`)으로 인덱스를 매일 갱신하는 파이프라인을 운영 중.

4단계 Operation:

1. 현재 버전 동일 문서 삭제 (재실행 대비 cleanup)
2. 신규 버전 문서 업로드 (~35,000건)
3. 이전 버전 문서 search → batch delete
4. 5초 대기 후 문서 수 검증 (`indexed_count == uploaded_count`)

## 2. 문제

**문제 A — Step 4 count 불일치:**
업로드 후 `get_count()`가 실제 업로드 건수와 다른 값을 반환하여 검증 실패.

**문제 B — Step 3 삭제 누락:**
이전 버전 문서 중 search가 전체를 반환하지 않아 일부가 삭제되지 않음.

## 3. 추정 원인

### 원인 1: Count 근사값 (→ 문제 A 설명)

Index가 stable 상태가 아닐 때 `include_total_count=True`의 count는 근사값이다.
Step 2 업로드 직후 + Step 3 삭제가 진행된 상태에서 Step 4가 실행되면 아직 인덱스가 안정화되지 않았을 수 있다.

참고: https://learn.microsoft.com/en-us/azure/search/search-pagination-page-layout

### 원인 2: Indeterminate ordering (→ 문제 B의 추정 원인 A)

- `SearchClient.search()`는 `SearchItemPaged`를 반환 → 내부적으로 pagination
- 현재 쿼리는 filter-only(full-text search 아님)이므로 모든 문서의 relevance score가 동일(1.0) → 명시적 `order_by`가 없으면 정렬 순서가 비결정적
- 비결정적 정렬 하에서 pagination의 각 page 요청 사이에 문서 순서가 바뀔 수 있어, 동일 문서가 중복 반환되거나 일부 문서가 건너뛰어질 수 있음

참고: https://learn.microsoft.com/en-us/azure/search/search-pagination-page-layout#ordering-results

### 원인 3: 인덱스 변경 중 Pagination (→ 문제 B의 추정 원인 B)

- Step 2(신규 버전 업로드)가 인덱스를 변경하면, 내부적으로 shard 간 문서 재배치가 발생
- 이 상태에서 Step 3의 search가 pagination을 순회하면, 각 page 요청 사이에 인덱스 내부 상태가 바뀌어 문서가 skip되거나 중복 반환될 수 있음

참고: https://learn.microsoft.com/en-us/azure/search/search-query-odata-orderby

### 세 원인의 관계

원인 2(indeterminate ordering)와 원인 3(인덱스 변경)은 각각 독립적으로 pagination 누락을 유발할 수 있는 추정 원인이다. 어느 쪽이 주 원인인지, 혹은 둘 다 필요한지를 실험으로 분리 확인한다. `order_by="id"`는 두 원인 모두에 대한 해결책이 될 수 있다. 원인 1(count 근사값)은 독립적인 현상으로 별도 검증한다.

## 4. 실험 설계

### 환경

- Azure AI Search: Standard SKU, 3 replicas, 1 partition, Korea Central
- 문서 수: 35,000건
- 필드: id (key, sortable), system_name (filterable, sortable), version (filterable, sortable), content (searchable)
- Python SDK: azure-search-documents 11.6.0

### Test 1: 기존 워크플로 재현 (`test1_workflow.py`)

기존 4단계 Operation을 그대로 재현하여 문제 A, B가 발생하는지 확인.

| 조건             | order_by        | 반복 |
| ---------------- | --------------- | ---- |
| without_order_by | 없음            | 3회  |
| with_order_by    | `order_by="id"` | 3회  |

**확인 항목:**

- Step 4: `indexed_count == uploaded_count` 여부
- Step 3: search로 찾은 이전 버전 문서 수 == 실제 이전 버전 문서 수 여부
- 워크플로 종료 후 남은 이전 버전 문서 수 (leftover)

### Test 2: Count 일관성 검증 (`test2_workflow.py`)

빈 인덱스에 35,000건을 insert한 직후, `search(include_total_count=True).get_count()`를 반복 조회하여 count 근사값 현상을 재현.

- 인덱스 cleanup → 35,000건 fresh insert → 즉시 count 조회 시작
- 2초 간격으로 조회, 3회 연속 기대값 일치 시 해당 시행 종료
- 위 과정을 3회 반복 (iterations=3)

**확인 항목:**

- 업로드 직후 `@odata.count`가 실제 건수와 다른 근사값을 반환하는지
- 몇 초 후 안정화되는지

### Test 3: Pagination 정확도 검증 (`test3_pagination.py`)

Pagination이 실제로 문서를 누락하는지 직접 확인.

**Test A — Stable Index:**
35,000건 업로드 → 15초 대기(안정화) → search로 전체 ID 수집 → expected ID set과 비교하여 누락/중복 체크

| 조건             | order_by        | 반복 |
| ---------------- | --------------- | ---- |
| without_order_by | 없음            | 3회  |
| with_order_by    | `order_by="id"` | 3회  |

**Test B — Mutating Index:**
인덱스 delete → recreate → 35,000건(old version) 업로드 → 15초 대기(안정화) → 새 버전 35,000건 업로드 완료 직후 즉시 old version search → 누락/중복 체크. 각 테스트(order_by 유/무)마다 인덱스를 독립적으로 초기화.

| 조건             | order_by        | 반복 |
| ---------------- | --------------- | ---- |
| without_order_by | 없음            | 3회  |
| with_order_by    | `order_by="id"` | 3회  |

## 5. 예상 결과

| 테스트                 | 조건                     | 예상                                     |
| ---------------------- | ------------------------ | ---------------------------------------- |
| 1 without_order_by     | 기존 워크플로            | 문제 A, B 재현 (count 불일치, 삭제 누락) |
| 1 with_order_by        | 기존 워크플로 + order_by | 문제 B 해결, 문제 A는 여전히 가능        |
| 2 count 조회           | 업로드 직후 count        | 근사값 반환 후 수초 내 안정화            |
| 3A stable              | 안정 인덱스              | order_by 유무 무관하게 정상              |
| 3A stable + order_by   | 안정 인덱스              | 정상                                     |
| 3B mutating            | 변경 중 인덱스           | 누락 발생                                |
| 3B mutating + order_by | 변경 중 인덱스           | 누락 없음                                |
