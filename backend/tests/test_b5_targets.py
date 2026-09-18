import json
import os
import sys

import httpx
import pytest

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


def test_equal_revision_hash_conflict_is_not_a_successful_skip():
    def respond(request):
        if "_doc" in request.url.path:
            return httpx.Response(200,json={"_source":{"revision":2,"semantic_hash":"different"}})
        return httpx.Response(200,json={"acknowledged":True})
    target = ElasticsearchTarget(_Settings(),transport=httpx.MockTransport(respond))
    with pytest.raises(ValueError,match="hash conflict"):
        target.apply(_snapshot(),"live")


def test_bulk_item_failure_propagates_despite_http_success():
    def respond(request):
        if "_doc" in request.url.path:
            return httpx.Response(404)
        if request.url.path.endswith("_bulk"):
            return httpx.Response(200,json={"errors":True,"items":[{"index":{"status":429}}]})
        return httpx.Response(200,json={"acknowledged":True})
    target = ElasticsearchTarget(_Settings(),transport=httpx.MockTransport(respond))
    with pytest.raises(RuntimeError,match="failed items"):
        target.apply(_snapshot(),"live")


def test_mapping_and_modes_have_explicit_bounds_and_scope():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json={"hits":{"hits":[]},"acknowledged":True})
    target = ElasticsearchTarget(_Settings(),transport=httpx.MockTransport(respond))
    target.search("public records","phrase",["d1"],{},generation="live")
    target.search("public records","fulltext",["d1"],{"include_leads":True},generation="live")
    mapping = json.loads(requests[0].content)
    assert requests[0].url.path=="/rhetoriq-documents-b5-live"
    assert mapping["mappings"]["dynamic"]=="strict"
    assert mapping["settings"]=={"number_of_shards":1,"number_of_replicas":0}
    queries = [json.loads(request.content) for request in requests if request.url.path.endswith("_search")]
    assert queries[0]["query"]["bool"]["must"][0]["match_phrase"]["text"]["slop"]==0
    assert "multi_match" in queries[1]["query"]["bool"]["must"][0]
    before = len(requests)
    assert target.search("records","fulltext",[],{})==[]
    assert len(requests)==before


def test_inventory_covers_more_than_one_thousand_documents():
    pages = [0]
    def respond(request):
        if not request.url.path.endswith("_search"):
            return httpx.Response(200,json={"acknowledged":True})
        page = pages[0]; pages[0] += 1
        count = 1000 if page==0 else 3
        hits = [{"sort":[f"d{page*1000+index:05}"],"_source":{"domain_id":f"d{page*1000+index:05}","revision":1,"semantic_hash":"h","eligible":True}} for index in range(count)]
        return httpx.Response(200,json={"hits":{"hits":hits}})
    target = ElasticsearchTarget(_Settings(),transport=httpx.MockTransport(respond))
    assert len(target.inventory("live"))==1003
