"""
Azure AI Search 인덱스 스키마 생성 및 테스트 데이터 적재.

기존 환경의 스키마(id, system_name, version, content)와 유사한 필드 구조의 인덱스를 생성하고,
지정한 건수의 더미 문서를 업로드한다.
"""

import logging
import os
from pathlib import Path

# .env 파일 로드 (같은 디렉토리의 .env에서 환경변수 읽기)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
)
from itertools import batched

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("azure").setLevel(logging.WARNING)

# 인덱스 이름: 환경변수로 오버라이드 가능, 기본값 "test-search-omission"
INDEX_NAME = os.environ.get("AZURE_AI_SEARCH_INDEX_NAME", "test-search-omission")


def get_index_client() -> SearchIndexClient:
    """인덱스 관리용 클라이언트 (인덱스 생성/삭제 등)"""
    return SearchIndexClient(
        endpoint=os.environ["AZURE_AI_SEARCH_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_AI_SEARCH_API_KEY"]),
    )


def get_search_client() -> SearchClient:
    """문서 업로드/검색/삭제용 클라이언트"""
    return SearchClient(
        endpoint=os.environ["AZURE_AI_SEARCH_ENDPOINT"],
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(os.environ["AZURE_AI_SEARCH_API_KEY"]),
    )


def create_index():
    """기존 환경의 스키마와 유사한 테스트 인덱스 생성."""
    client = get_index_client()

    fields = [
        # 문서 고유 키 — order_by="id" 사용을 위해 sortable 설정
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, sortable=True),
        # 시스템 구분자 (e.g. "product", "product-caution") — 필터/정렬 가능
        SimpleField(
            name="system_name",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        # 날짜 버전 (e.g. "20260415") — 필터/정렬 가능
        SimpleField(
            name="version",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        # 문서 본문 — full-text 검색 가능
        SearchableField(name="content", type=SearchFieldDataType.String),
    ]

    index = SearchIndex(name=INDEX_NAME, fields=fields)
    # create_or_update: 이미 있으면 스키마 업데이트, 없으면 생성
    client.create_or_update_index(index)
    logger.info(f"인덱스 '{INDEX_NAME}' 생성/업데이트 완료.")


def generate_documents(system_name: str, version: str, count: int) -> list[dict]:
    """기존 환경의 스키마에 맞는 더미 문서 생성.

    ID 형식: "{system_name}-{version}-{번호:06d}" (e.g. "product-20260414-000123")
    이렇게 하면 나중에 expected ID set과 비교하여 누락 여부를 정확히 확인할 수 있다.
    """
    docs = []
    for i in range(count):
        docs.append(
            {
                "id": f"{system_name}-{version}-{i:06d}",
                "system_name": system_name,
                "version": version,
                "content": f"Test document {i} for {system_name} version {version}. This is filler content to simulate a real document payload.",
            }
        )
    return docs


def upload_documents(docs: list[dict], batch_size: int = 512):
    """문서를 배치 단위로 업로드 (기존과 동일한 방식).

    기존 환경과 동일하게 batch_size=512로 나누어 upload_documents 호출.
    실패 건이 있으면 즉시 RuntimeError.
    """
    client = get_search_client()
    total = 0
    for batch in batched(docs, batch_size):
        results = client.upload_documents(list(batch))
        failed = [r for r in results if not r.succeeded]
        if failed:
            raise RuntimeError(f"업로드 실패: {len(failed)}건")
        total += len(results)
        logger.info(f"업로드 {total}/{len(docs)}")
    return total


def seed_data(system_name: str, version: str, count: int):
    """인덱스 생성 후 테스트 데이터 적재."""
    create_index()
    docs = generate_documents(system_name, version, count)
    uploaded = upload_documents(docs)
    logger.info(f"시딩 완료: {uploaded}건, system_name={system_name}, version={version}")
    return uploaded


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed test data into Azure AI Search")
    parser.add_argument("--system-name", default="product", help="system_name value")
    parser.add_argument("--version", required=True, help="Version string (e.g. 20260414)")
    parser.add_argument("--count", type=int, default=10000, help="Number of documents")
    args = parser.parse_args()

    seed_data(args.system_name, args.version, args.count)
