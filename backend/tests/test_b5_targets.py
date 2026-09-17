import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from demo_data import DEMO_DOCUMENTS
from services.b5_targets import ElasticsearchTarget, Neo4jTarget


class _Settings:
    ELASTICSEARCH_URL = "http://elastic.test"
    ELASTICSEARCH_USERNAME = "user"
    ELASTICSEARCH_PASSWORD = "pass"
    B5_QUERY_TIMEOUT_SECONDS = 3


def _snapshot():
    return {"snapshot_id": "s1", "kind": "document", "domain_id": "d1", "revision": 2, "semantic_hash": "sha256:s1", "operation": "upsert", "eligible": True, "data": {"document": DEMO_DOCUMENTS[0].model_dump(mode="json"), "enrichment": None, "processing": {}, "source_kind": "retrieved", "withdrawn": False}}


def test_elasticsearch_stale_guard_and_search():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET" and "_doc" in request.url.path:
            return httpx.Response(200, json={"_source": {"document_id": "doc_001", "revision": 3, "semantic_hash": "sha256:new"}})
        if request.url.path.endswith("_search"):
            return httpx.Response(200, json={"hits": {"hits": [{"_id": "doc_001", "_score": 1.0, "_source": {"document_id": "doc_001", "revision": 3, "semantic_hash": "sha256:new"}}]}})
        return httpx.Response(200, json={"acknowledged": True})

    target = ElasticsearchTarget(_Settings(), transport=httpx.MockTransport(handler))
    result = target.apply(_snapshot(), "live")
    assert result["skipped"] == ["doc_001"]
    assert target.search("energy", "exact", ["doc_001"], {}, generation="live")[0]["document_id"] == "doc_001"
    assert any(request.url.path.endswith("_search") for request in requests)


def test_targets_keep_unexpected_repair_bounded_to_target_scope():
    es = ElasticsearchTarget(type("S", (), {})())
    neo = Neo4jTarget(type("S", (), {})())
    assert es.remove_unexpected("d", "live")["status"] == "unconfigured"
    assert neo.remove_unexpected("d", "live")["status"] == "unconfigured"
