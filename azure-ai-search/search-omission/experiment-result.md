# Azure AI Search 데이터 누락 문제 — 실험 결과

> 실험 일시: 2026-04-16  
> 환경: Azure AI Search Standard SKU, 3 replicas, 1 partition, Korea Central  
> 문서 수: 35,000건  
> SDK: azure-search-documents 11.6.0

---

## Test 1: 기존 워크플로 재현

기존 4단계 워크플로(cleanup → upload → search & delete old → count 검증)를 `order_by` 유/무로 각 3회 실행.

### 결과

| iter | 조건             | step4 count | count 일치 | leftover (이전 버전 잔여) |
| ---- | ---------------- | ----------- | ---------- | ------------------------- |
| 1    | without_order_by | 35,000      | O          | 0                         |
| 2    | without_order_by | 40,970      | **X**      | **5,970**                 |
| 3    | without_order_by | 42,083      | **X**      | **7,083**                 |
| 1    | with_order_by    | 35,000      | O          | 0                         |
| 2    | with_order_by    | 35,000      | O          | 0                         |
| 3    | with_order_by    | 35,000      | O          | 0                         |

### 분석

- `order_by` 없이 실행 시 3회 중 2회에서 이전 버전 문서가 5,970~7,083건 삭제되지 않고 잔존 → **Step 4 count 불일치의 직접 원인**
- iter 1은 첫 실행이라 이전 버전 데이터가 없어 문제 미발생. iter 2~3에서 실제 워크플로 조건이 충족되어 문제 재현
- `order_by="id"` 적용 시 3/3 모두 leftover=0, count 정확 일치

---

## Test 2: Count 근사값 검증

빈 인덱스에 35,000건을 fresh insert 한 직후 `search(filter=..., include_total_count=True).get_count()`를 2초 간격으로 반복 조회. 3회 연속 기대값 일치 시 시행 종료 × 3회 시행.

### 결과

| 시행 | 1차 조회 count | 오차   | 안정화 시점       | 안정화 소요 |
| ---- | -------------- | ------ | ----------------- | ----------- |
| 1    | 33,933         | -1,067 | 2차 조회 (~2.2초) | ~2초        |
| 2    | 31,995         | -3,005 | 2차 조회 (~2.6초) | ~3초        |
| 3    | 33,437         | -1,563 | 2차 조회 (~2.9초) | ~3초        |

### 분석

- 35,000건 insert 직후 `@odata.count`가 실제보다 **1,067~3,005건 적은 근사값**을 반환
- **2~3초 후 안정화**되어 이후 조회에서는 정확한 35,000 반환
- 이는 Azure AI Search의 documented behavior:
  - > _"@odata.count will be less than or equal to the actual number of items that match the query. ... In general, @odata.count is an approximation."_
  - 출처: [Search pagination page layout — Ordering results](https://learn.microsoft.com/en-us/azure/search/search-pagination-page-layout#ordering-results)

---

## Test 3: Pagination 정확도 검증

### Test A — Stable Index

35,000건 업로드 → 15초 안정화 대기 → search pagination으로 전체 ID 수집 → expected ID set과 비교.

| iter | 조건             | returned | unique | missing | dup |
| ---- | ---------------- | -------- | ------ | ------- | --- |
| 1    | without_order_by | 35,000   | 35,000 | 0       | 0   |
| 2    | without_order_by | 35,000   | 35,000 | 0       | 0   |
| 3    | without_order_by | 35,000   | 35,000 | 0       | 0   |
| 1    | with_order_by    | 35,000   | 35,000 | 0       | 0   |
| 2    | with_order_by    | 35,000   | 35,000 | 0       | 0   |
| 3    | with_order_by    | 35,000   | 35,000 | 0       | 0   |

**안정된 인덱스에서는 `order_by` 유무와 무관하게 pagination이 정확하다.**

### Test B — Mutating Index

인덱스 delete/recreate → old version 35,000건 업로드 → 15초 안정화 → new version 35,000건 업로드 완료 직후 즉시 old version search. 각 테스트마다 인덱스를 독립적으로 초기화.

| iter | 조건             | returned | unique | missing   | dup   | 누락률 |
| ---- | ---------------- | -------- | ------ | --------- | ----- | ------ |
| 1    | without_order_by | 35,000   | 27,291 | **7,709** | 7,709 | 22.0%  |
| 2    | without_order_by | 35,000   | 28,531 | **6,469** | 6,469 | 18.5%  |
| 3    | without_order_by | 35,000   | 28,231 | **6,769** | 6,769 | 19.3%  |
| 1    | with_order_by    | 35,000   | 35,000 | **0**     | 0     | 0%     |
| 2    | with_order_by    | 35,000   | 35,000 | **0**     | 0     | 0%     |
| 3    | with_order_by    | 35,000   | 35,000 | **0**     | 0     | 0%     |

### 분석

- `order_by` 없이 인덱스 변경 중 pagination 시, 전체 35,000건 중 **6,469~7,709건(18~22%)이 누락**되고 동일 수의 중복이 발생
  - `returned_count`는 35,000으로 동일하지만, 일부 문서가 중복 반환되고 다른 문서가 누락됨
  - **원인**: 현재 쿼리는 filter-only이므로 명시적 `order_by` 없이는 정렬 순서가 비결정적이다. 이 상황에서 new version 업로드로 인덱스 내부에서 shard 간 문서 재배치가 발생하면, pagination 각 page 요청 사이에 문서 순서가 변동하여 cursor가 문서를 건너뛰거나 중복 방문
  - Test A(Stable)에서 인덱스 변경이 없을 때는 `order_by` 없이도 누락 미발생 → **인덱스 변경이 누락의 직접 원인**임을 확인
- `order_by="id"` 적용 시 **3/3 모두 missing=0, dup=0** — deterministic ordering이 인덱스 변경 중에도 pagination 안정성을 보장

---

## 결론

### 1. 삭제 누락 (문제 B)

추정 원인 A(indeterminate ordering)와 추정 원인 B(인덱스 변경 중 pagination)의 실험 결과:

- **추정 원인 A (indeterminate ordering) → 원인 아님**
  - Test 3A(stable index)에서 `order_by` 없이도 6/6 모두 누락 0건
  - 비결정적 정렬 자체만으로는 pagination 누락이 발생하지 않음
- **추정 원인 B (인덱스 변경 중 pagination) → 직접 원인**
  - Test 3B(mutating index)에서 `order_by` 없이 3/3 모두 누락 발생 (6,469~7,709건)
  - 신규 버전 업로드(Step 2)로 인덱스 내부에서 shard 간 문서 재배치가 발생하고, `order_by` 없이 pagination을 순회하면 각 page 요청 사이에 문서 순서가 변동하여 skip/중복 발생
  - search 결과에서 누락된 문서는 삭제 대상에 포함되지 않음 → 이전 버전 문서 잔존
- **해결책**: `order_by="id"` 추가로 deterministic ordering 보장 → **실험 결과 100% 해결** (9/9 모두 정상)

### 2. Count 근사값 (문제 A)

- **근본 원인**: 인덱스가 안정화되지 않은 상태에서 `@odata.count`가 근사값을 반환
- **영향**: 업로드 직후 count 검증 시 실제 건수보다 적은 값을 반환하여 false negative
- **안정화 시간**: 2~3초 이내
- **해결책**: count 검증 전 충분한 대기 시간 확보, 또는 retry 로직 적용

### 3. 두 문제의 복합 효과

기존 파이프라인에서는 문제 B(삭제 누락)로 인해 이전 버전 문서가 잔존하고, 문제 A(count 근사값)로 인해 검증 단계에서 불일치가 감지됨. 문제 B가 **주된 실패 원인**이며, 문제 A는 **검증 시점의 timing 이슈**이다.

---

## 권고 사항

### 필수 (삭제 누락 해결)

`search()` 호출 시 `order_by` 파라미터를 반드시 지정하여 deterministic pagination을 보장.

```python
# Before (indeterminate ordering)
results = client.search(filter="system_name eq 'product' and version eq '20260414'")

# After (deterministic ordering)
results = client.search(
    filter="system_name eq 'product' and version eq '20260414'",
    order_by="id"
)
```

### 권장 (count 안정성 확보)

업로드/삭제 후 count 검증 시 즉시 검증하지 말고, 안정화 대기 또는 retry 로직 적용.

```python
# 방법 1: 충분한 대기
time.sleep(5)
count = client.search(filter=..., include_total_count=True).get_count()

# 방법 2: retry with tolerance
for _ in range(5):
    count = client.search(filter=..., include_total_count=True).get_count()
    if count == expected:
        break
    time.sleep(2)
```

---

## 참고자료

| 주제                                           | 링크                                                                                          |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Pagination & count 근사값                      | https://learn.microsoft.com/en-us/azure/search/search-pagination-page-layout                  |
| Ordering results (indeterminate ordering 설명) | https://learn.microsoft.com/en-us/azure/search/search-pagination-page-layout#ordering-results |
| $orderby OData 구문                            | https://learn.microsoft.com/en-us/azure/search/search-query-odata-orderby                     |
| Search Documents REST API                      | https://learn.microsoft.com/en-us/rest/api/searchservice/documents/search-post                |
