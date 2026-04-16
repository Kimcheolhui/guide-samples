"""
Test 2: Count 일관성 검증.

한 버전의 문서를 업로드한 뒤, 10초 간격으로 반복 조회하여
document count가 일관되게 유지되는지 확인한다.

재현 목표:
  - 35,000건 업로드 후 34,991 / 34,997 등 실제보다 적은 count가 반환되는 현상
  - replica 간 전파 지연으로 인한 count 근사값 문제 여부

검증 방법:
  - 업로드 완료 후 충분히 대기(안정화)
  - 이후 10초 간격으로 count를 반복 조회
  - 기대값(업로드 건수)과 다른 값이 나오면 기록
"""

import json
import logging
import os
import time

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

from setup_index import generate_documents, upload_documents, INDEX_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("azure").setLevel(logging.WARNING)


def get_search_client() -> SearchClient:
    """Search client 생성."""
    return SearchClient(
        endpoint=os.environ["AZURE_AI_SEARCH_ENDPOINT"],
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(os.environ["AZURE_AI_SEARCH_API_KEY"]),
    )


def get_document_count(system_name: str) -> int:
    """문서 수 조회"""
    client = get_search_client()
    results = client.search(
        filter=f"system_name eq '{system_name}'",
        include_total_count=True,
    )
    return results.get_count()


def run_count_check(
    system_name: str,
    version: str,
    doc_count: int,
    check_interval: float,
    consecutive_target: int = 3,
    max_wait: int = 300,
) -> dict:
    """
    문서 업로드 후 count를 반복 조회하여 일관성을 검증.

    절차:
      1. 기존 인덱스에 문서 업로드
      2. 업로드 직후 check_interval 초 간격으로 count 조회
      3. expected와 consecutive_target 회 연속 일치하면 종료
    """
    # 1단계: 기존 인덱스에 문서 업로드
    logger.info(f"기존 인덱스에 {doc_count}건 업로드 시작 (version={version})")
    docs = generate_documents(system_name, version, doc_count)
    upload_documents(docs)
    logger.info(f"업로드 완료: {doc_count}건")

    # 2단계: 업로드 직후부터 count 조회 — N회 연속 일치하면 종료
    logger.info(f"{check_interval}초 간격으로 count 조회 시작 ({consecutive_target}회 연속 일치 시 종료, 최대 {max_wait}초)")
    measurements = []
    start_time = time.time()
    check_num = 0
    consecutive_matches = 0

    while True:
        check_num += 1
        elapsed = time.time() - start_time
        count = get_document_count(system_name)
        match = count == doc_count
        measurements.append({
            "check_number": check_num,
            "elapsed_sec": round(elapsed, 1),
            "count": count,
            "expected": doc_count,
            "match": match,
        })

        if match:
            consecutive_matches += 1
        else:
            consecutive_matches = 0

        status = "✓" if match else "✗"
        logger.info(f"  [{check_num}] {elapsed:.1f}초 count={count}, expected={doc_count} {status} (연속 {consecutive_matches}/{consecutive_target})")

        if consecutive_matches >= consecutive_target:
            logger.info(f"  ✓ {check_num}회 조회, {elapsed:.1f}초 만에 {consecutive_target}회 연속 일치 확인")
            break
        if elapsed >= max_wait:
            logger.info(f"  ⚠ {max_wait}초 초과 — {consecutive_target}회 연속 일치 달성 실패")
            break
        time.sleep(check_interval)

    # 3단계: 결과 집계
    mismatches = [m for m in measurements if not m["match"]]
    counts = [m["count"] for m in measurements]

    result = {
        "system_name": system_name,
        "version": version,
        "doc_count": doc_count,
        "check_interval_sec": check_interval,
        "consecutive_target": consecutive_target,
        "total_checks": check_num,
        "total_elapsed_sec": round(time.time() - start_time, 1),
        "matched": consecutive_matches >= consecutive_target,
        "consecutive_matches": consecutive_matches,
        "mismatch_count": len(mismatches),
        "min_count": min(counts),
        "max_count": max(counts),
        "measurements": measurements,
    }

    return result


def cleanup(system_name: str, version: str):
    """테스트 문서 전체 삭제."""
    client = get_search_client()
    filter_expr = f"system_name eq '{system_name}' and version eq '{version}'"
    results = client.search(filter=filter_expr, select=["id"], order_by="id")
    ids = [doc["id"] for doc in results]
    if ids:
        from itertools import batched
        for batch in batched(ids, 512):
            client.delete_documents([{"id": id_} for id_ in batch])
        logger.info(f"정리 완료: {len(ids)}건 삭제")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test count consistency after upload")
    parser.add_argument("--system-name", default="product",
                        help="시스템 이름 (문서 ID prefix)")
    parser.add_argument("--version", default="20260414",
                        help="문서 버전")
    parser.add_argument("--count", type=int, default=10000,
                        help="업로드할 문서 수")
    parser.add_argument("--check-interval", type=float, default=2,
                        help="count 조회 간격 (초)")
    parser.add_argument("--consecutive", type=int, default=3,
                        help="연속 일치 횟수 (이 횟수만큼 연속 일치하면 시행 종료)")
    parser.add_argument("--iterations", type=int, default=3,
                        help="시행 횟수")
    parser.add_argument("--max-wait", type=int, default=300,
                        help="시행 당 최대 대기 시간 (초)")
    parser.add_argument("--output", default="results/test2_workflow.json",
                        help="결과 저장 경로")
    args = parser.parse_args()

    # .env 파일 로드
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    logger.info("=" * 60)
    logger.info("Test 2: Count 일관성 검증")
    logger.info("=" * 60)
    logger.info(f"문서 수: {args.count}, 조회 간격: {args.check_interval}초")
    logger.info(f"연속 일치 목표: {args.consecutive}회, 시행 횟수: {args.iterations}회")

    all_results = []
    for iteration in range(1, args.iterations + 1):
        logger.info(f"\n--- 시행 {iteration}/{args.iterations} ---")
        result = run_count_check(
            system_name=args.system_name,
            version=args.version,
            doc_count=args.count,
            check_interval=args.check_interval,
            consecutive_target=args.consecutive,
            max_wait=args.max_wait,
        )
        all_results.append(result)

        # 정리
        logger.info("테스트 문서 정리 중...")
        cleanup(args.system_name, args.version)
        if iteration < args.iterations:
            logger.info("다음 시행 전 10초 대기...")
            time.sleep(10)

    # 결과 저장
    summary = {
        "iterations": args.iterations,
        "doc_count": args.count,
        "check_interval_sec": args.check_interval,
        "consecutive_target": args.consecutive,
        "results": all_results,
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"결과 저장: {args.output}")

    # 요약 출력
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for i, r in enumerate(all_results, 1):
        status = "✓" if r["matched"] else "✗"
        print(f"  시행 {i}: {r['total_checks']}회 조회, {r['total_elapsed_sec']}초 소요, "
              f"불일치 {r['mismatch_count']}회, count {r['min_count']}~{r['max_count']} {status}")
    all_matched = all(r["matched"] for r in all_results)
    if all_matched:
        print(f"  ✓ 모든 시행에서 {args.consecutive}회 연속 일치 — count 근사값 현상 미재현")
    else:
        failed = sum(1 for r in all_results if not r["matched"])
        print(f"  ⚠ {failed}/{args.iterations} 시행에서 연속 일치 실패 — count 근사값 현상 재현")
