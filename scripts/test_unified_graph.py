"""
Test unified knowledge graph queries

This script demonstrates querying the unified graph with entities.
"""
import asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db, db_manager
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_entity_nodes():
    """Test querying entity nodes"""
    print("\n=== Entity Nodes in Unified Graph ===")

    db_manager.initialize()

    async for db in get_db():
        try:
            # Get all entity nodes
            result = await db.execute(
                select(CrossSourceNode)
                .where(CrossSourceNode.source == "derived")
                .limit(5)
            )
            nodes = result.scalars().all()

            for node in nodes:
                print(f"\n{node.node_type.value.upper()}: {node.title}")
                print(f"  Canonical ID: {node.canonical_id}")
                print(f"  Occurrences: {node.entity_metadata.get('occurrence_count', 'N/A')}")
                print(f"  Channels: {len(node.entity_metadata.get('channels', []))}")

            return nodes
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()


async def test_entity_relationships():
    """Test querying entity relationships"""
    print("\n\n=== Entity Relationships in Unified Graph ===")

    db_manager.initialize()

    async for db in get_db():
        try:
            # Get entity-to-entity edges
            result = await db.execute(
                select(CrossSourceEdge)
                .where(CrossSourceEdge.edge_type == EdgeType.CO_OCCURS_WITH)
                .limit(10)
            )
            edges = result.scalars().all()

            for edge in edges:
                print(f"\n{edge.source_node_id} --[{edge.edge_type.value}]--> {edge.target_node_id}")
                print(f"  Confidence: {edge.confidence}")
                print(f"  Co-occurrences: {edge.edge_metadata.get('co_occurrence_count', 'N/A')}")

            return edges
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()


async def test_cross_source_query():
    """Test querying across different sources"""
    print("\n\n=== Cross-Source Node Types ===")

    db_manager.initialize()

    async for db in get_db():
        try:
            # Count nodes by type
            result = await db.execute(
                select(CrossSourceNode.node_type, CrossSourceNode.source)
            )

            type_counts = {}
            for node_type, source in result:
                key = f"{source}:{node_type.value}"
                type_counts[key] = type_counts.get(key, 0) + 1

            print("\nNode counts by source and type:")
            for key, count in sorted(type_counts.items()):
                print(f"  {key}: {count}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """Run all tests"""
    print("=" * 60)
    print("UNIFIED KNOWLEDGE GRAPH - TEST QUERIES")
    print("=" * 60)

    await test_entity_nodes()
    await test_entity_relationships()
    await test_cross_source_query()

    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
