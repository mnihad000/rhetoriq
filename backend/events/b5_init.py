from config import get_settings
from services.b5_repository import ProjectionRepository
from services.b5_targets import ElasticsearchTarget, Neo4jTarget


def main():
    settings=get_settings()
    generation=ProjectionRepository(settings.persistence_target).manifest()["generation"]
    for cls in (ElasticsearchTarget,Neo4jTarget):
        adapter=cls(settings)
        try:
            adapter.initialize(generation)
        finally:
            adapter.close()


if __name__=="__main__":
    main()
