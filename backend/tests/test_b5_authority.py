"""Use actual durable snapshots to check stopped-worker withdrawals."""
from config import Settings
from events.b5_acceptance import build_seed_documents,persist_acceptance_investigation
from services.b5_repository import ProjectionRepository
from services.b5_search import SearchService
from services.b5_cache import B5Cache
from tests.test_b5_cache import FakeRedis


def seeded(tmp_path):
    target = str(tmp_path/"authority.db")
    repository = ProjectionRepository(target)
    documents = build_seed_documents("authority",2)
    snapshots = [repository.record_document(document,source_event_id="seed:"+document.id) for document in documents]
    investigation = persist_acceptance_investigation(repository,"authority",documents)["snapshot"]
    settings = Settings(_env_file=None,DATABASE_URL="",INVESTIGATION_DB_PATH=target,ENABLE_B5_RETRIEVAL=False)
    return repository,documents,snapshots,investigation,settings


def test_withdrawal_invalidates_real_scoped_search_graph_and_path_caches(tmp_path):
    repository,documents,snapshots,investigation,settings = seeded(tmp_path)
    for snapshot in snapshots:
        for target in ("elasticsearch","neo4j","minilm"):
            repository.mark_delivery(target,snapshot)
    repository.mark_delivery("neo4j",investigation)
    class Elastic:
        def search(self,*args,**kwargs):
            return [{"document_id":snapshot["domain_id"],"revision":snapshot["revision"],"semantic_hash":snapshot["semantic_hash"],"score":1} for snapshot in snapshots]
    class Neo:
        def read_graph(self,*args,**kwargs):
            return {"revision":investigation["revision"],"semantic_hash":investigation["semantic_hash"],"nodes":[{"id":document.id,"kind":"document","document_id":document.id,"label":document.title} for document in documents],
                "edges":[{"id":"reference","source":documents[0].id,"target":documents[1].id,"relationship":"references","evidence_class":"observed","method":"fixture","method_version":"v1","evidence":{},"limitations":[]}],"limitations":[]}
    service = SearchService(settings=settings,repository=repository,elastic=Elastic(),neo4j=Neo(),cache=B5Cache(redis_client=FakeRedis()))
    identity = investigation["domain_id"]
    assert len(service.search("public records",mode="fulltext",investigation_id=identity)["results"])==2
    assert service.search("public records",mode="fulltext",investigation_id=identity)["cached"]
    assert service.graph(identity)["edges"]
    assert service.graph(identity)["cached"]
    assert service.paths(identity,documents[0].id,documents[1].id)["paths"]
    repository.withdraw(documents[1].id,operation_id="operator:remove",reason="fixture stopped workers")
    assert [row["document_id"] for row in service.search("public records",mode="fulltext",investigation_id=identity)["results"]]==[documents[0].id]
    graph = service.graph(identity)
    assert documents[1].id not in {node["id"] for node in graph["nodes"]}
    assert not graph["edges"]
    assert not service.paths(identity,documents[0].id,documents[1].id)["paths"]


def test_empty_healthy_index_differs_from_pending_coverage(tmp_path):
    repository,documents,snapshots,investigation,settings = seeded(tmp_path)
    class Elastic:
        def search(self,*args,**kwargs):
            return []
    service = SearchService(settings=settings,repository=repository,elastic=Elastic())
    identity = investigation["domain_id"]
    pending = service.search("absent",mode="fulltext",investigation_id=identity)
    assert not pending["results"] and pending["pending"] and not pending["complete"]
    for snapshot in snapshots:
        repository.mark_delivery("elasticsearch",snapshot)
    healthy = service.search("absent",mode="fulltext",investigation_id=identity)
    assert not healthy["results"] and not healthy["pending"] and healthy["complete"]
