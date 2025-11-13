"""
Fix node_type enum values in cross_source_nodes table

The database has lowercase values ('topic', 'technology') but the SQLAlchemy
enum expects uppercase enum names ('TOPIC', 'TECHNOLOGY').

This script updates the data to match the enum definition.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.core.logging import get_logger
from sqlalchemy import text

logger = get_logger(__name__)


async def main():
    """Fix node_type enum values"""

    team_id = "T0420EE1VQ8"

    logger.info("fix_node_types_started", team_id=team_id)

    # Initialize database
    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Get current node_types
            result = await db.execute(text("""
                SELECT canonical_id, node_type
                FROM cross_source_nodes
                WHERE team_id = :team_id
            """), {"team_id": team_id})

            nodes = result.fetchall()

            print(f"\nFound {len(nodes)} nodes")
            print("\nCurrent node_type values:")
            node_types = {}
            for canonical_id, node_type in nodes:
                node_types[node_type] = node_types.get(node_type, 0) + 1

            for nt, count in sorted(node_types.items()):
                print(f"  {nt}: {count}")

            # Map lowercase to uppercase
            type_mapping = {
                'topic': 'TOPIC',
                'technology': 'TECHNOLOGY',
                'person': 'PERSON',
                'project': 'PROJECT',
                'organization': 'ORGANIZATION',
                'location': 'LOCATION',
                'event': 'EVENT',
                'message': 'MESSAGE',
                'thread': 'THREAD',
                'file': 'FILE',
                'code_snippet': 'CODE_SNIPPET'
            }

            # Update each type
            for old_type, new_type in type_mapping.items():
                await db.execute(text("""
                    UPDATE cross_source_nodes
                    SET node_type = :new_type
                    WHERE node_type = :old_type
                      AND team_id = :team_id
                """), {
                    "new_type": new_type,
                    "old_type": old_type,
                    "team_id": team_id
                })

            await db.commit()

            # Verify
            result = await db.execute(text("""
                SELECT node_type, COUNT(*) as count
                FROM cross_source_nodes
                WHERE team_id = :team_id
                GROUP BY node_type
            """), {"team_id": team_id})

            updated_types = result.fetchall()

            print(f"\n✅ SUCCESS! Updated node_type values:")
            for node_type, count in updated_types:
                print(f"  {node_type}: {count}")

        except Exception as e:
            logger.error("fix_node_types_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}\n")
            await db.rollback()
            raise

        break


if __name__ == "__main__":
    asyncio.run(main())
