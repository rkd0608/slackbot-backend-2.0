"""
Test Graph-Enhanced Retrieval

Demonstrates graph-based retrieval capabilities
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.graph_enhanced_retrieval import get_graph_enhanced_retrieval
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_graph_expansion():
    """Test expanding context via graph traversal"""
    print("\n" + "="*60)
    print("TEST: Graph Expansion from Seed Nodes")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            retrieval = get_graph_enhanced_retrieval(db)

            # Simulate initial vector search results
            # In real usage, these come from vector search
            initial_results = [
                {
                    'canonical_id': 'entity:concept:migration',
                    'score': 0.95,
                    'title': 'migration'
                },
                {
                    'canonical_id': 'entity:technical:database',
                    'score': 0.90,
                    'title': 'database'
                }
            ]

            print(f"\nInitial results: {len(initial_results)}")
            for r in initial_results:
                print(f"  - {r['title']} (score: {r['score']})")

            # Expand via graph
            expansion = await retrieval.retrieve_with_graph_expansion(
                query="database migration",
                team_id=team_id,
                initial_results=initial_results,
                expansion_depth=2,
                max_expanded=20
            )

            print(f"\nExpanded results: {len(expansion['expanded_results'])}")
            for r in expansion['expanded_results'][:5]:
                print(f"  - {r.get('title')} (depth: {r.get('expansion_depth')}, score: {r.get('score', 0):.3f})")

            print(f"\nStats:")
            print(f"  Nodes explored: {expansion['stats'].get('nodes_explored', 0)}")
            print(f"  Edges traversed: {expansion['stats'].get('edges_traversed', 0)}")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_entity_relationships():
    """Test retrieving by entity relationships"""
    print("\n" + "="*60)
    print("TEST: Entity Relationship Retrieval")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            retrieval = get_graph_enhanced_retrieval(db)

            # Find everything related to specific entities
            results = await retrieval.retrieve_by_entity_relationships(
                entities=['database', 'migration'],
                team_id=team_id,
                max_results=10
            )

            print(f"\nResults related to 'database' and 'migration': {len(results)}")
            for r in results[:5]:
                print(f"\n  {r.get('title')}")
                print(f"    Source: {r.get('source')}")
                print(f"    Score: {r.get('score', 0):.3f}")
                if r.get('relationships'):
                    print(f"    Relationships:")
                    for rel in r.get('relationships', [])[:2]:
                        print(f"      - {rel.get('edge_type')} (from: {rel.get('from_entity')})")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_path_finding():
    """Test finding paths between entities"""
    print("\n" + "="*60)
    print("TEST: Path Finding Between Entities")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            retrieval = get_graph_enhanced_retrieval(db)

            # Find paths between two entities
            paths = await retrieval.find_paths_between_entities(
                entity1='entity:concept:migration',
                entity2='entity:technical:database',
                team_id=team_id,
                max_depth=3,
                max_paths=3
            )

            print(f"\nPaths found: {len(paths)}")
            for i, path in enumerate(paths, 1):
                print(f"\nPath {i} (strength: {path['path_strength']:.3f}):")
                print(f"  Nodes: {' → '.join([n['title'] for n in path['nodes']])}")
                print(f"  Edges: {len(path['edges'])}")
                for edge in path['edges']:
                    print(f"    - {edge['edge_type']} (conf: {edge['confidence']:.2f})")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("GRAPH-ENHANCED RETRIEVAL TEST SUITE")
    print("="*60)

    await test_graph_expansion()
    await test_entity_relationships()
    await test_path_finding()

    print("\n" + "="*60)
    print("✅ All tests completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
