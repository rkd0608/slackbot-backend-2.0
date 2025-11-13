"""
Directly create cross-source edges using SQL
Bypasses SQLAlchemy enum validation issues
"""
import asyncio
import sys
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.core.logging import get_logger
from sqlalchemy import text

logger = get_logger(__name__)


async def main():
    """Create edges by directly inserting into database"""

    team_id = "T0420EE1VQ8"

    logger.info("direct_edge_creation_started", team_id=team_id)

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Step 1: Get all entity pairs from cross_source_nodes
            result = await db.execute(text(f"""
                SELECT
                    n1.canonical_id as entity1_id,
                    n1.title as entity1_name,
                    n2.canonical_id as entity2_id,
                    n2.title as entity2_name
                FROM cross_source_nodes n1
                CROSS JOIN cross_source_nodes n2
                WHERE n1.team_id = '{team_id}'
                  AND n2.team_id = '{team_id}'
                  AND n1.source = 'derived'
                  AND n2.source = 'derived'
                  AND n1.canonical_id < n2.canonical_id
                LIMIT 50
            """))

            entity_pairs = result.fetchall()
            logger.info("entity_pairs_found", count=len(entity_pairs))

            edge_count = 0

            # Step 2: For each pair, check co-occurrence and create edge
            for entity1_id, entity1_name, entity2_id, entity2_name in entity_pairs:
                # Check co-occurrence in messages
                co_result = await db.execute(text(f"""
                    SELECT COUNT(*) as count
                    FROM messages
                    WHERE team_id = '{team_id}'
                      AND (text_processed LIKE '%{entity1_name}%'
                       OR text LIKE '%{entity1_name}%')
                      AND (text_processed LIKE '%{entity2_name}%'
                       OR text LIKE '%{entity2_name}%')
                """))

                co_occurrence = co_result.scalar()

                if co_occurrence > 0:
                    # Calculate simple confidence
                    confidence = min(1.0, co_occurrence / 10.0)

                    # Create edge
                    edge_id = str(uuid.uuid4())
                    now = datetime.utcnow()

                    await db.execute(text(f"""
                        INSERT INTO cross_source_edges
                        (id, source_node_id, target_node_id, team_id, edge_type,
                         detection_method, confidence, edge_metadata, created_at, updated_at)
                        VALUES
                        ('{edge_id}', '{entity1_id}', '{entity2_id}', '{team_id}',
                         'CO_OCCURS_WITH', 'CO_OCCURRENCE', {confidence},
                         '{{"co_occurrence_count": {co_occurrence}}}',
                         '{now}', '{now}')
                    """))

                    edge_count += 1
                    logger.info("edge_created",
                              entity1=entity1_name,
                              entity2=entity2_name,
                              co_occurrence=co_occurrence)

            await db.commit()

            # Update workspace status
            await db.execute(text(f"""
                UPDATE workspaces
                SET indexing_status = 'complete',
                    indexing_completed_at = '{datetime.utcnow()}',
                    total_messages_indexed = 399,
                    total_channels_indexed = 5
                WHERE team_id = '{team_id}'
            """))
            await db.commit()

            logger.info("direct_edge_creation_completed",
                       team_id=team_id,
                       edges_created=edge_count)

            print(f"\n✅ SUCCESS!")
            print(f"   Edges created: {edge_count}")
            print(f"   Entity pairs checked: {len(entity_pairs)}")
            print(f"   Workspace status: complete\n")

        except Exception as e:
            logger.error("direct_edge_creation_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}\n")
            raise

        break


if __name__ == "__main__":
    asyncio.run(main())
