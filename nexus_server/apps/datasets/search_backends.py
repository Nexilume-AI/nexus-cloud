from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions

from apps.common.models import SoftDeleteModel

from .models import Dataset, DatasetFile, DatasetFileIndex


class DatasetSearchBackend:
    name = ""

    def ensure_index(self) -> None:
        return None

    def index_file(self, *, dataset_file: DatasetFile, index: DatasetFileIndex) -> None:
        raise NotImplementedError

    def search(self, *, tenant_id: str, dataset_ids: list[str], keyword: str) -> list[dict[str, Any]]:
        raise NotImplementedError


class DatabaseDatasetSearchBackend(DatasetSearchBackend):
    name = "database"

    def index_file(self, *, dataset_file: DatasetFile, index: DatasetFileIndex) -> None:
        return None

    def search(self, *, tenant_id: str, dataset_ids: list[str], keyword: str) -> list[dict[str, Any]]:
        indexes = (
            DatasetFileIndex.objects.select_related("dataset", "file")
            .filter(
                tenant_id=tenant_id,
                dataset_id__in=dataset_ids,
                index_status=DatasetFileIndex.STATUS_INDEXED,
                content_text__icontains=keyword,
            )
            .order_by("-updated_at")[:50]
        )
        return [
            {
                "dataset": item.dataset,
                "file": item.file,
                "matches": [{"snippet": snippet(item.content_text, keyword)}],
            }
            for item in indexes
        ]


class OpenSearchDatasetSearchBackend(DatasetSearchBackend):
    name = "opensearch"

    def ensure_index(self) -> None:
        try:
            self.request("HEAD", f"/{index_name()}")
            return
        except HTTPError as exc:
            if exc.code != 404:
                raise
        mapping = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "tenant_id": {"type": "keyword"},
                    "project_id": {"type": "keyword"},
                    "dataset_id": {"type": "keyword"},
                    "file_id": {"type": "keyword"},
                    "file_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "content_text": {"type": "text"},
                    "sha256": {"type": "keyword"},
                    "created_at": {"type": "date"},
                }
            },
        }
        self.request("PUT", f"/{index_name()}", payload=mapping)

    def index_file(self, *, dataset_file: DatasetFile, index: DatasetFileIndex) -> None:
        if index.index_status != DatasetFileIndex.STATUS_INDEXED:
            return
        self.ensure_index()
        document = {
            "tenant_id": str(dataset_file.tenant_id),
            "project_id": str(dataset_file.project_id or ""),
            "dataset_id": str(dataset_file.dataset_id),
            "file_id": str(dataset_file.id),
            "file_name": dataset_file.file_name,
            "content_text": index.content_text,
            "sha256": dataset_file.sha256,
            "created_at": timezone.now().isoformat(),
        }
        self.request("PUT", f"/{index_name()}/_doc/{dataset_file.id}?refresh=true", payload=document)

    def search(self, *, tenant_id: str, dataset_ids: list[str], keyword: str) -> list[dict[str, Any]]:
        if not dataset_ids:
            return []
        self.ensure_index()
        payload = {
            "size": 50,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"tenant_id": tenant_id}},
                        {"terms": {"dataset_id": dataset_ids}},
                    ],
                    "must": [
                        {
                            "multi_match": {
                                "query": keyword,
                                "fields": ["content_text", "file_name"],
                            }
                        }
                    ],
                }
            },
            "highlight": {
                "fields": {
                    "content_text": {"fragment_size": 160, "number_of_fragments": 1},
                }
            },
        }
        response = self.request("POST", f"/{index_name()}/_search", payload=payload)
        hits = response.get("hits", {}).get("hits", []) if isinstance(response, dict) else []
        file_ids = [hit.get("_source", {}).get("file_id") for hit in hits if hit.get("_source", {}).get("file_id")]
        files = {
            str(item.id): item
            for item in DatasetFile.objects.select_related("dataset").filter(
                id__in=file_ids,
                tenant_id=tenant_id,
                dataset_id__in=dataset_ids,
                status=SoftDeleteModel.STATUS_ACTIVE,
            )
        }
        results: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source", {})
            dataset_file = files.get(str(source.get("file_id", "")))
            if dataset_file is None:
                continue
            highlights = hit.get("highlight", {}).get("content_text") or []
            content = strip_highlight(highlights[0]) if highlights else str(source.get("content_text", ""))
            results.append(
                {
                    "dataset": dataset_file.dataset,
                    "file": dataset_file,
                    "matches": [{"snippet": snippet(content, keyword)}],
                }
            )
        return results

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{settings.NEXUS_OPENSEARCH_URL.rstrip('/')}{path}",
            data=body,
            method=method,
            headers=self.headers(payload=payload is not None),
        )
        with urlopen(request, timeout=settings.NEXUS_OPENSEARCH_TIMEOUT_SECONDS) as response:
            if method == "HEAD":
                return {}
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def headers(self, *, payload: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if payload:
            headers["Content-Type"] = "application/json"
        if settings.NEXUS_OPENSEARCH_USERNAME:
            credentials = f"{settings.NEXUS_OPENSEARCH_USERNAME}:{settings.NEXUS_OPENSEARCH_PASSWORD}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
        return headers


def get_dataset_search_backend(name: str | None = None) -> DatasetSearchBackend:
    backend_name = (name or settings.NEXUS_DATASET_SEARCH_BACKEND or "database").lower()
    if backend_name == "database":
        return DatabaseDatasetSearchBackend()
    if backend_name == "opensearch":
        return OpenSearchDatasetSearchBackend()
    raise exceptions.ValidationError("Unsupported dataset search backend.")


def index_name() -> str:
    return quote(settings.NEXUS_OPENSEARCH_INDEX, safe="")


def strip_highlight(value: str) -> str:
    return value.replace("<em>", "").replace("</em>", "")


def snippet(text: str, keyword: str, radius: int = 80) -> str:
    lower_text = text.lower()
    lower_keyword = keyword.lower()
    position = lower_text.find(lower_keyword)
    if position < 0:
        return text[: radius * 2]
    start = max(position - radius, 0)
    end = min(position + len(keyword) + radius, len(text))
    return text[start:end].replace("\r", " ").replace("\n", " ")
