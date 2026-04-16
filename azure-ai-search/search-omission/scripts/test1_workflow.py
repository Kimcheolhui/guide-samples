"""
Test 1: 기존 워크플로 재현.

기존의 4단계 Operation을 그대로 재현하여 다음을 검증:
  - 문제 A: Step 4 count 불일치 (indexed_count != uploaded_count)
  - 문제 B: Step 3 삭제 누락 (이전 버전 문서가 모두 검색되지 않음)

order_by="id" 유/무로 결과를 비교한다.
"""

import json
import logging
import os
import time
from itertools import batched

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

from setup_index import create_index, generate_documents, upload_documents, INDEX_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("azure").setLevel(logging.WARNING)


def get_client() -> SearchClient:
    return SearchClient(
        endpoint=os.environ["AZURE_AI_SEARCH_ENDPOINT"],
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(os.environ["AZURE_AI_SEARCH_API_KEY"]),
    )


def search_ids(client: SearchClient, filter_expr: str, use_order_by: bool) -> list[str]:
    """필터 조건으로 검색하여 모든 ID를 수집 (pagination 자동 순회)."""
    kwargs = {"filter": filter_expr, "select": ["id"]}
    if use_order_by:
        kwargs["order_by"] = "id"
    results = client.search(**kwargs)
    return [doc["id"] for doc in results]


def delete_by_ids(client: SearchClient, ids: list[str], batch_size: int = 512) -> int:
    """수집한 ID 목록을 배치 단위로 삭제."""
    total = 0
    for batch in batched(ids, batch_size):
        docs = [{"id": id_} for id_ in batch]
        results = client.delete_documents(docs)
        failed = [r for r in results if not r.succeeded]
        if failed:
            raise RuntimeError(f"Delete failed: {len(failed)} docs")
        total += len(results)
    return total


def run_workflow(
    system_name: str,
    prev_version: str,
    current_version: str,
    doc_count: int,
    use_order_by: bool,
    propagation_wait_sec: int = 5,
) -> dict:
    """
    기존의 4단계 Operation을 그대로 재현.

    각 단계의 건수와 불일치 정보를 담은 result dict를 반환.
    """
    client = get_client()
    result = {
        "system_name": system_name,
        "prev_version": prev_version,
        "current_version": current_version,
        "doc_count": doc_count,
        "use_order_by": use_order_by,
    }

    # ── Step 1: 현재 버전 문서 정리 (재실행 대비) ──
    logger.info("Step 1: 현재 버전 문서 정리")
    filter_expr = f"system_name eq '{system_name}' and version eq '{current_version}'"
    ids = search_ids(client, filter_expr, use_order_by)
    result["step1_found"] = len(ids)
    if ids:
        delete_by_ids(client, ids)
    logger.info(f"  Step 1: found and deleted {len(ids)} docs")

    # ── Step 2: 신규 버전 문서 업로드 ──
    logger.info("Step 2: 신규 버전 문서 업로드")
    docs = generate_documents(system_name, current_version, doc_count)
    total_uploaded = upload_documents(docs)
    result["step2_uploaded"] = total_uploaded
    logger.info(f"  Step 2: uploaded {total_uploaded} docs")

    # ── Step 3: 이전 버전 문서 삭제 ──
    logger.info("Step 3: 이전 버전 문서 삭제")
    filter_expr = f"system_name eq '{system_name}' and version lt '{current_version}'"
    ids = search_ids(client, filter_expr, use_order_by)
    result["step3_found_to_delete"] = len(ids)
    if ids:
        deleted = delete_by_ids(client, ids)
        result["step3_deleted"] = deleted
    else:
        result["step3_deleted"] = 0
    logger.info(f"  Step 3: found {result['step3_found_to_delete']}, deleted {result['step3_deleted']}")

    # ── Step 4: 문서 수 검증 ──
    logger.info(f"Step 4: {propagation_wait_sec}초 대기 후 문서 수 검증")
    time.sleep(propagation_wait_sec)
    results = client.search(
        filter=f"system_name eq '{system_name}'",
        include_total_count=True,
    )
    indexed_count = results.get_count()
    result["step4_indexed_count"] = indexed_count
    result["step4_count_match"] = indexed_count == total_uploaded
    logger.info(f"  Step 4: indexed={indexed_count}, uploaded={total_uploaded}, match={result['step4_count_match']}")

    # ── 추가 검증: 이전 버전 잔여 문서 확인 ──
    time.sleep(2)
    filter_expr = f"system_name eq '{system_name}' and version lt '{current_version}'"
    leftover_ids = search_ids(client, filter_expr, True)  # 정확한 확인을 위해 항상 order_by 사용
    result["leftover_old_version_count"] = len(leftover_ids)
    logger.info(f"  Leftover old-version docs: {len(leftover_ids)}")

    return result


def run_test(
    system_name: str,
    prev_version: str,
    current_version: str,
    doc_count: int,
    iterations: int,
    propagation_wait_sec: int = 5,
) -> list[dict]:
    """
    전체 테스트 실행: 이전 버전 시딩 → order_by 유/무로 워크플로 실행.
    """
    all_results = []

    for i in range(iterations):
        for use_order_by in [False, True]:
            label = "with_order_by" if use_order_by else "without_order_by"
            logger.info(f"\n{'='*60}")
            logger.info(f"Iteration {i+1}/{iterations} - {label}")
            logger.info(f"{'='*60}")

            # 이전 버전 데이터 시딩
            logger.info(f"이전 버전 데이터 시딩: {doc_count}건, version={prev_version}")
            create_index()
            prev_docs = generate_documents(system_name, prev_version, doc_count)
            upload_documents(prev_docs)

            # 인덱스 안정화 대기 후 워크플로 실행
            logger.info("인덱스 안정화 대기 30초...")
            time.sleep(30)

            # Run the workflow
            result = run_workflow(
                system_name=system_name,
                prev_version=prev_version,
                current_version=current_version,
                doc_count=doc_count,
                use_order_by=use_order_by,
                propagation_wait_sec=propagation_wait_sec,
            )
            result["iteration"] = i + 1
            result["label"] = label
            all_results.append(result)

            # 정리: 다음 반복을 위해 모든 문서 삭제
            logger.info("정리: 다음 반복을 위해 모든 문서 삭제")
            for version in [prev_version, current_version]:
                filter_expr = f"system_name eq '{system_name}' and version eq '{version}'"
                ids = search_ids(get_client(), filter_expr, True)
                if ids:
                    delete_by_ids(get_client(), ids)

            # 삭제 완료 검증: 문서가 0건이 될 때까지 대기
            logger.info("정리 검증: 모든 문서가 삭제되었는지 확인 중...")
            for attempt in range(12):  # 최대 60초 대기
                time.sleep(5)
                remaining = 0
                for version in [prev_version, current_version]:
                    filter_expr = f"system_name eq '{system_name}' and version eq '{version}'"
                    ids = search_ids(get_client(), filter_expr, True)
                    remaining += len(ids)
                    # 잔여 문서가 있으면 추가 삭제 시도
                    if ids:
                        delete_by_ids(get_client(), ids)
                if remaining == 0:
                    logger.info(f"  정리 완료 (대기 {(attempt+1)*5}초)")
                    break
                logger.info(f"  잔여 문서 {remaining}건, 재시도 ({attempt+1}/12)")
            else:
                logger.warning(f"  경고: 60초 후에도 {remaining}건 잔여. 다음 반복에 영향 가능.")

            # 추가 안정화 대기
            time.sleep(10)

    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Reproduce customer workflow issue")
    parser.add_argument("--system-name", default="product")
    parser.add_argument("--prev-version", default="20260414")
    parser.add_argument("--current-version", default="20260415")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--wait", type=int, default=5, help="Propagation wait seconds")
    parser.add_argument("--output", default="results/test1_workflow.json")
    args = parser.parse_args()

    results = run_test(
        system_name=args.system_name,
        prev_version=args.prev_version,
        current_version=args.current_version,
        doc_count=args.count,
        iterations=args.iterations,
        propagation_wait_sec=args.wait,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Results saved to {args.output}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        print(
            f"  [{r['label']}] iter={r['iteration']} "
            f"uploaded={r['step2_uploaded']} indexed={r['step4_indexed_count']} "
            f"count_match={r['step4_count_match']} "
            f"step3_found={r['step3_found_to_delete']} "
            f"leftover_old={r['leftover_old_version_count']}"
        )
