"""
Test 3: Pagination 정확도 검증.

Search pagination이 모든 문서를 정확히 반환하는지 검증:
  A) Stable Index: 인덱스 안정 상태에서 order_by 유/무 비교
  B) Mutating Index: 업로드가 동시에 진행되는 중 search 수행

확인 항목:
  - filter-only 쿼리(full-text search 아님)에서 pagination 누락이 발생하는지
  - order_by="id"가 누락을 해결하는지
  - 인덱스 변경 중 pagination이 문서를 누락하는지
"""

import json
import logging
import os
import time
from itertools import batched

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

from setup_index import (
    create_index,
    generate_documents,
    get_index_client,
    upload_documents,
    get_search_client,
    INDEX_NAME,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("azure").setLevel(logging.WARNING)


def search_all_ids(client: SearchClient, filter_expr: str, use_order_by: bool) -> list[str]:
    """필터 조건으로 검색하여 모든 ID를 수집 (pagination 자동 순회)."""
    kwargs = {"filter": filter_expr, "select": ["id"]}
    if use_order_by:
        kwargs["order_by"] = "id"
    results = client.search(**kwargs)
    return [doc["id"] for doc in results]


def check_pagination_accuracy(
    system_name: str,
    version: str,
    expected_count: int,
    use_order_by: bool,
) -> dict:
    """전체 문서를 검색하고 누락/중복 ID를 확인.

    expected ID set과 실제 반환된 ID set을 비교하여
    누락(missing), 중복(duplicate), 예상 외(unexpected) 건수를 반환.
    """
    client = get_search_client()
    filter_expr = f"system_name eq '{system_name}' and version eq '{version}'"

    ids = search_all_ids(client, filter_expr, use_order_by)

    unique_ids = set(ids)
    missing = expected_count - len(unique_ids)
    duplicates = len(ids) - len(unique_ids)

    # 예상 ID set을 생성하여 정확한 비교
    expected_ids = {f"{system_name}-{version}-{i:06d}" for i in range(expected_count)}
    actually_missing = expected_ids - unique_ids
    unexpected = unique_ids - expected_ids

    return {
        "use_order_by": use_order_by,
        "expected_count": expected_count,
        "returned_count": len(ids),
        "unique_count": len(unique_ids),
        "missing_count": len(actually_missing),
        "duplicate_count": duplicates,
        "unexpected_count": len(unexpected),
        "sample_missing": sorted(list(actually_missing))[:10],
    }


def test_stable_index(system_name: str, version: str, doc_count: int, iterations: int) -> list[dict]:
    """
    Test A: 안정 상태 인덱스에서의 pagination 정확도 검증.

    데이터 시딩 → 15초 대기(안정화) → order_by 유/무로 검색 → 누락 확인.
    """
    results = []
    logger.info("=== Test A: Stable Index Pagination ===")

    # 테스트 데이터 시딩
    create_index()
    docs = generate_documents(system_name, version, doc_count)
    upload_documents(docs)

    # 인덱스 전파 완료 대기
    logger.info("인덱스 안정화 대기 15초...")
    time.sleep(15)

    for i in range(iterations):
        for use_order_by in [False, True]:
            label = "with_order_by" if use_order_by else "without_order_by"
            logger.info(f"  Stable iter {i+1} - {label}")
            result = check_pagination_accuracy(system_name, version, doc_count, use_order_by)
            result["test"] = "stable"
            result["iteration"] = i + 1
            result["label"] = label
            results.append(result)
            logger.info(
                f"    returned={result['returned_count']} unique={result['unique_count']} "
                f"missing={result['missing_count']} dup={result['duplicate_count']}"
            )

    return results


def test_mutating_index(
    system_name: str,
    old_version: str,
    new_version: str,
    doc_count: int,
    iterations: int,
) -> list[dict]:
    """
    Test B: 인덱스 변경 중 pagination 정확도 검증.

    기존 시나리오 재현: 새 버전 업로드와 동시에 이전 버전 문서를 search.
    업로드로 인한 인덱스 변경이 pagination 결과에 영향을 주는지 확인.
    """
    results = []
    logger.info("=== Test B: Mutating Index Pagination ===")

    for i in range(iterations):
        for use_order_by in [False, True]:
            label = "with_order_by" if use_order_by else "without_order_by"

            # 인덱스 완전 초기화 (동일한 초기 조건 보장)
            try:
                get_index_client().delete_index(INDEX_NAME)
            except Exception:
                pass
            time.sleep(2)
            create_index()

            # 이전 버전 데이터 시딩
            old_docs = generate_documents(system_name, old_version, doc_count)
            upload_documents(old_docs)
            logger.info("인덱스 안정화 대기 15초...")
            time.sleep(15)

            logger.info(f"  Mutating iter {i+1} - {label}")

            # 새 버전 문서 업로드 (mutation trigger)
            new_docs = generate_documents(system_name, new_version, doc_count)
            upload_documents(new_docs)

            # 업로드 직후 이전 버전 검색 (대기 없이 즉시)
            result = check_pagination_accuracy(system_name, old_version, doc_count, use_order_by)
            result["test"] = "mutating"
            result["iteration"] = i + 1
            result["label"] = label
            results.append(result)
            logger.info(
                f"    returned={result['returned_count']} unique={result['unique_count']} "
                f"missing={result['missing_count']} dup={result['duplicate_count']}"
            )

    return results


def cleanup_all(system_name: str, versions: list[str]):
    """테스트 문서 전체 삭제."""
    client = get_search_client()
    for version in versions:
        filter_expr = f"system_name eq '{system_name}' and version eq '{version}'"
        ids = search_all_ids(client, filter_expr, True)
        if ids:
            for batch in batched(ids, 512):
                client.delete_documents([{"id": id_} for id_ in batch])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test pagination accuracy")
    parser.add_argument("--system-name", default="product")
    parser.add_argument("--old-version", default="20260414")
    parser.add_argument("--new-version", default="20260415")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output", default="results/test3_pagination.json")
    args = parser.parse_args()

    all_results = []

    # Test A: stable index
    stable_results = test_stable_index(args.system_name, args.old_version, args.count, args.iterations)
    all_results.extend(stable_results)

    # Cleanup
    cleanup_all(args.system_name, [args.old_version])
    time.sleep(10)

    # Test B: mutating index
    mutating_results = test_mutating_index(
        args.system_name, args.old_version, args.new_version, args.count, args.iterations
    )
    all_results.extend(mutating_results)

    # Cleanup
    cleanup_all(args.system_name, [args.old_version, args.new_version])

    # Save
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2)
    logger.info(f"Results saved to {args.output}")

    # Summary
    print("\n" + "=" * 70)
    print("PAGINATION TEST SUMMARY")
    print("=" * 70)
    for r in all_results:
        status = "OK" if r["missing_count"] == 0 and r["duplicate_count"] == 0 else "ISSUE"
        print(
            f"  [{status}] {r['test']:8s} {r['label']:16s} iter={r['iteration']} "
            f"expected={r['expected_count']} returned={r['returned_count']} "
            f"missing={r['missing_count']} dup={r['duplicate_count']}"
        )
