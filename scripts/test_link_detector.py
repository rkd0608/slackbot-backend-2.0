"""
Test the enhanced CrossSourceLinkDetector

Runs link detection on existing nodes to build cross-source connections
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.cross_source_link_detector import get_link_detector
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Test link detector on workspace"""

    team_id = "T0420EE1VQ8"

    logger.info("link_detector_test_started", team_id=team_id)

    # Initialize database
    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            detector = get_link_detector(db)

            # Run batch detection for Slack nodes
            print("\n=== Processing Slack nodes ===")
            slack_stats = await detector.batch_detect_missing_links(
                team_id=team_id,
                source_filter="slack",
                limit=50
            )

            print(f"Slack nodes processed: {slack_stats['nodes_processed']}")
            print(f"Links created: {slack_stats['links_created']}")
            print(f"By type: {slack_stats['by_type']}")

            # Run batch detection for derived entity nodes
            print("\n=== Processing entity nodes ===")
            entity_stats = await detector.batch_detect_missing_links(
                team_id=team_id,
                source_filter="derived",
                limit=50
            )

            print(f"Entity nodes processed: {entity_stats['nodes_processed']}")
            print(f"Links created: {entity_stats['links_created']}")
            print(f"By type: {entity_stats['by_type']}")

            # Summary
            total_links = slack_stats['links_created'] + entity_stats['links_created']
            print(f"\n✅ Total new links created: {total_links}")

        except Exception as e:
            logger.error("link_detector_test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}\n")
            raise

        break


if __name__ == "__main__":
    asyncio.run(main())
