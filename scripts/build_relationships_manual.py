"""
Manually build entity relationships and cross-source edges
Run this to complete the indexing workflow manually
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.workers.initial_indexing import initial_indexing_worker
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Build relationships for a workspace"""

    team_id = "T0420EE1VQ8"

    logger.info("manual_relationship_building_started", team_id=team_id)

    # Initialize database manager (it's synchronous but runs async internally)
    db_manager.initialize()

    # Get database session
    async for db in db_manager.get_session():
        try:
            # Step 1: Build entity relationships
            logger.info("building_entity_relationships", team_id=team_id)
            relationship_count = await initial_indexing_worker._build_entity_relationships(
                team_id, db
            )
            logger.info("entity_relationships_built",
                       team_id=team_id,
                       count=relationship_count)

            # Step 2: Calculate user expertise
            logger.info("calculating_user_expertise", team_id=team_id)
            expertise_count = await initial_indexing_worker._calculate_user_expertise(
                team_id, db
            )
            logger.info("user_expertise_calculated",
                       team_id=team_id,
                       count=expertise_count)

            # Step 3: Detect significant events
            logger.info("detecting_significant_events", team_id=team_id)
            event_count = await initial_indexing_worker._detect_significant_events(
                team_id, db
            )
            logger.info("significant_events_detected",
                       team_id=team_id,
                       count=event_count)

            # Step 4: Update workspace status
            from app.models.workspace import Workspace
            from sqlalchemy import select, update
            from datetime import datetime

            result = await db.execute(
                select(Workspace).where(Workspace.team_id == team_id)
            )
            workspace = result.scalar_one()

            workspace.indexing_status = "complete"
            workspace.indexing_completed_at = datetime.utcnow()
            workspace.total_messages_indexed = 399
            workspace.total_channels_indexed = 5

            await db.commit()

            logger.info(
                "manual_relationship_building_completed",
                team_id=team_id,
                relationships=relationship_count,
                expertise_areas=expertise_count,
                events=event_count
            )

            print(f"\n✅ SUCCESS!")
            print(f"   Relationships created: {relationship_count}")
            print(f"   Expertise areas: {expertise_count}")
            print(f"   Significant events: {event_count}")
            print(f"   Workspace status: complete\n")

        except Exception as e:
            logger.error("manual_relationship_building_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}\n")
            raise

        # Break after first session (we only need one)
        break


if __name__ == "__main__":
    asyncio.run(main())
