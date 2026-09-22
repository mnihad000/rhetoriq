import argparse

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


def check():
    """Verify the B5 manifest and backing stores without changing them."""
    settings = get_settings()
    manifest = ProjectionRepository(settings.persistence_target).manifest()
    if not manifest.get("generation"):
        raise RuntimeError("B5 generation manifest is missing")
    generation = manifest["generation"]
    for cls in (ElasticsearchTarget, Neo4jTarget):
        adapter = cls(settings)
        try:
            health = adapter.health()
            if health.get("status") != "healthy":
                raise RuntimeError(f"{cls.__name__} is not healthy")
            if not adapter.initialized(generation):
                raise RuntimeError(f"{cls.__name__} is not initialized for generation {generation}")
        finally:
            adapter.close()


if __name__=="__main__":
    parser = argparse.ArgumentParser(description="Initialize or verify B5 projection targets")
    parser.add_argument("--check", action="store_true", help="Verify without changing targets")
    args = parser.parse_args()
    check() if args.check else main()
