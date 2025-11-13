"""
Test script to reproduce and fix the enum deserialization issue
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType
from sqlalchemy import select, text
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_read_entity_nodes():
    """Test reading entity nodes directly via ORM"""
    print("\n" + "="*60)
    print("TEST: Read Entity Nodes via ORM")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Try to read entity nodes
            result = await db.execute(
                select(CrossSourceNode).where(
                    CrossSourceNode.team_id == team_id,
                    CrossSourceNode.source == 'derived'
                ).limit(5)
            )
            nodes = result.scalars().all()

            print(f"\n✓ Successfully loaded {len(nodes)} entity nodes:")
            for node in nodes:
                print(f"  - {node.canonical_id} (type: {node.node_type})")

        except Exception as e:
            print(f"\n✗ FAILED to read entity nodes via ORM")
            print(f"  Error: {type(e).__name__}: {str(e)}")
            logger.error("orm_read_failed", error=str(e), exc_info=True)

        break


async def test_read_edges():
    """Test reading edges via ORM"""
    print("\n" + "="*60)
    print("TEST: Read Edges via ORM")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Try to read edges
            result = await db.execute(
                select(CrossSourceEdge).where(
                    CrossSourceEdge.team_id == team_id
                ).limit(5)
            )
            edges = result.scalars().all()

            print(f"\n✓ Successfully loaded {len(edges)} edges:")
            for edge in edges:
                print(f"  - {edge.source_node_id} -> {edge.target_node_id}")
                print(f"    Type: {edge.edge_type}, Confidence: {edge.confidence}")

        except Exception as e:
            print(f"\n✗ FAILED to read edges via ORM")
            print(f"  Error: {type(e).__name__}: {str(e)}")
            logger.error("orm_edge_read_failed", error=str(e), exc_info=True)

        break


async def test_raw_sql():
    """Test reading via raw SQL (bypass ORM)"""
    print("\n" + "="*60)
    print("TEST: Read via Raw SQL")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Raw SQL query
            result = await db.execute(
                text("""
                    SELECT canonical_id, node_type, title
                    FROM cross_source_nodes
                    WHERE team_id = :team_id AND source = 'derived'
                    LIMIT 5
                """),
                {"team_id": team_id}
            )
            rows = result.fetchall()

            print(f"\n✓ Successfully loaded {len(rows)} entity nodes via raw SQL:")
            for row in rows:
                print(f"  - {row[0]} (type: {row[1]}, title: {row[2]})")

        except Exception as e:
            print(f"\n✗ FAILED raw SQL query")
            print(f"  Error: {type(e).__name__}: {str(e)}")

        break


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("ENUM DESERIALIZATION TEST SUITE")
    print("="*60)

    # Test 1: Read entity nodes
    await test_read_entity_nodes()

    # Test 2: Read edges
    await test_read_edges()

    # Test 3: Raw SQL (should always work)
    await test_raw_sql()

    print("\n" + "="*60)
    print("✓ All tests completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
