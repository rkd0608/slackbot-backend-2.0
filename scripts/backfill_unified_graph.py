"""
Backfill Unified Knowledge Graph

This script migrates existing Slack messages and GitHub content into the new
cross-source knowledge graph (CrossSourceNode and CrossSourceEdge tables).

Usage:
    python scripts/backfill_unified_graph.py --team-id T1234567 [--dry-run]
"""
import asyncio
import argparse
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.message import Message
from app.models.integration import ExternalContent
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.services.cross_source_link_service import CrossSourceLinkService

# Import to register all adapters
import app.integrations.adapters

logger = get_logger(__name__)


async def backfill_slack_messages(
    db: AsyncSession,
    team_id: str,
    dry_run: bool = False,
    limit: int = None
) -> dict:
    """
    Migrate existing Slack messages to CrossSourceNode

    Args:
        db: Database session
        team_id: Team ID to migrate
        dry_run: If True, don't actually write to database
        limit: Optional limit on number of messages to migrate

    Returns:
        Dict with migration stats
    """
    logger.info("backfill_slack_messages_started", team_id=team_id, dry_run=dry_run)

    stats = {
        'total_messages': 0,
        'migrated': 0,
        'skipped_already_exists': 0,
        'skipped_no_content': 0,
        'failed': 0,
        'errors': []
    }

    try:
        # Get all messages for this team
        query = select(Message).where(Message.team_id == team_id)
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        messages = result.scalars().all()

        stats['total_messages'] = len(messages)

        logger.info(f"Found {len(messages)} Slack messages to migrate")

        for message in messages:
            try:
                # Create canonical ID
                canonical_id = f"slack:{message.message_id}"

                # Check if already migrated
                existing = await db.execute(
                    select(CrossSourceNode).where(
                        CrossSourceNode.canonical_id == canonical_id
                    )
                )
                if existing.scalar_one_or_none():
                    stats['skipped_already_exists'] += 1
                    continue

                # Skip messages without content
                if not message.text:
                    stats['skipped_no_content'] += 1
                    continue

                # Determine node type
                node_type = NodeType.THREAD if message.thread_ts else NodeType.MESSAGE

                # Create CrossSourceNode
                node = CrossSourceNode(
                    canonical_id=canonical_id,
                    team_id=team_id,
                    source="slack",
                    source_id=message.message_id,
                    node_type=node_type,

                    # Content
                    title=message.text[:100] if message.text else "Untitled message",
                    content=message.text,
                    summary=None,  # Could add AI-generated summary later

                    # Access
                    url=f"https://slack.com/archives/{message.channel_id}/p{message.message_id.replace('.', '')}",
                    visibility="team",  # Slack messages are team-visible

                    # Attribution
                    author=message.user_name,
                    author_id=message.user_id,

                    # Timestamps
                    created_at=message.timestamp,
                    updated_at=message.timestamp,
                    indexed_at=datetime.utcnow(),

                    # Context
                    channel_context={
                        'channel_id': message.channel_id,
                        'channel_name': message.channel_name,
                        'is_thread': bool(message.thread_ts),
                        'thread_ts': message.thread_ts
                    },
                    tags=[],  # Could extract hashtags later

                    # Embeddings (preserve existing if available)
                    vector_id_semantic=message.vector_id,
                    vector_id_code=None,  # Slack messages don't have code embeddings

                    # Source-specific metadata
                    source_metadata={
                        'reactions': message.reactions or [],
                        'reply_count': message.reply_count or 0,
                        'file_ids': message.file_ids or []
                    }
                )

                if not dry_run:
                    db.add(node)
                    stats['migrated'] += 1
                else:
                    stats['migrated'] += 1  # Count what would be migrated

                # Commit in batches of 100
                if stats['migrated'] % 100 == 0:
                    if not dry_run:
                        await db.commit()
                    logger.info(f"Migrated {stats['migrated']} messages so far...")

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append({
                    'message_id': message.message_id,
                    'error': str(e)
                })
                logger.error(
                    "message_migration_failed",
                    message_id=message.message_id,
                    error=str(e)
                )

        # Final commit
        if not dry_run:
            await db.commit()

        logger.info("backfill_slack_messages_completed", stats=stats)
        return stats

    except Exception as e:
        logger.error("backfill_slack_messages_failed", error=str(e))
        if not dry_run:
            await db.rollback()
        raise


async def backfill_github_content(
    db: AsyncSession,
    team_id: str,
    dry_run: bool = False,
    limit: int = None
) -> dict:
    """
    Migrate existing GitHub content (ExternalContent) to CrossSourceNode

    Args:
        db: Database session
        team_id: Team ID to migrate
        dry_run: If True, don't actually write to database
        limit: Optional limit on number of items to migrate

    Returns:
        Dict with migration stats
    """
    logger.info("backfill_github_content_started", team_id=team_id, dry_run=dry_run)

    stats = {
        'total_content': 0,
        'migrated': 0,
        'skipped_already_exists': 0,
        'skipped_no_content': 0,
        'failed': 0,
        'errors': []
    }

    try:
        # Get all GitHub content for this team
        query = select(ExternalContent).where(
            and_(
                ExternalContent.team_id == team_id,
                ExternalContent.source_type == "github",
                ExternalContent.is_deleted == "false"
            )
        )
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        contents = result.scalars().all()

        stats['total_content'] = len(contents)

        logger.info(f"Found {len(contents)} GitHub items to migrate")

        for content in contents:
            try:
                # Create canonical ID
                # source_id format: "owner/repo/type/id" or "owner/repo/blob/sha/path"
                canonical_id = f"github:{content.source_id}"

                # Check if already migrated
                existing = await db.execute(
                    select(CrossSourceNode).where(
                        CrossSourceNode.canonical_id == canonical_id
                    )
                )
                if existing.scalar_one_or_none():
                    stats['skipped_already_exists'] += 1
                    continue

                # Skip content without text
                if not content.content and not content.title:
                    stats['skipped_no_content'] += 1
                    continue

                # Determine node type based on content type or source_id
                node_type = NodeType.CODE_FILE  # Default for GitHub content

                # Create CrossSourceNode
                node = CrossSourceNode(
                    canonical_id=canonical_id,
                    team_id=team_id,
                    source="github",
                    source_id=content.source_id,
                    node_type=node_type,

                    # Content
                    title=content.title or "Untitled",
                    content=content.content,
                    summary=None,

                    # Access
                    url=content.source_url or f"https://github.com/{content.source_id}",
                    visibility=content.repository_visibility or "public",

                    # Attribution
                    author=content.author,
                    author_id=content.author_external_id,

                    # Timestamps
                    created_at=content.created_at_source or datetime.utcnow(),
                    updated_at=content.updated_at_source,
                    indexed_at=content.last_indexed_at or datetime.utcnow(),

                    # Context
                    project_context={
                        'repository_id': content.repository_id,
                        'repository_full_name': content.repository_full_name,
                        'repository_visibility': content.repository_visibility,
                        'language': content.language
                    },
                    tags=[content.language] if content.language else [],

                    # Embeddings (preserve existing)
                    vector_id_semantic=content.vector_id_semantic,
                    vector_id_code=content.vector_id,  # Code embedding

                    # Source-specific metadata
                    source_metadata={
                        'content_type': content.content_type,
                        'language': content.language,
                        'source_metadata': content.source_metadata or {}
                    }
                )

                if not dry_run:
                    db.add(node)
                    stats['migrated'] += 1
                else:
                    stats['migrated'] += 1

                # Commit in batches of 100
                if stats['migrated'] % 100 == 0:
                    if not dry_run:
                        await db.commit()
                    logger.info(f"Migrated {stats['migrated']} GitHub items so far...")

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append({
                    'content_id': content.id,
                    'source_id': content.source_id,
                    'error': str(e)
                })
                logger.error(
                    "github_content_migration_failed",
                    content_id=content.id,
                    error=str(e)
                )

        # Final commit
        if not dry_run:
            await db.commit()

        logger.info("backfill_github_content_completed", stats=stats)
        return stats

    except Exception as e:
        logger.error("backfill_github_content_failed", error=str(e))
        if not dry_run:
            await db.rollback()
        raise


async def detect_and_create_links(
    db: AsyncSession,
    team_id: str,
    dry_run: bool = False,
    limit: int = None
) -> dict:
    """
    Detect links between migrated nodes and create edges

    Args:
        db: Database session
        team_id: Team ID
        dry_run: If True, don't actually write to database
        limit: Optional limit on number of nodes to process

    Returns:
        Dict with link detection stats
    """
    logger.info("detect_and_create_links_started", team_id=team_id, dry_run=dry_run)

    stats = {
        'total_nodes': 0,
        'processed': 0,
        'total_links_detected': 0,
        'total_edges_created': 0,
        'failed': 0
    }

    try:
        # Get all nodes for this team (recently migrated)
        query = select(CrossSourceNode).where(
            and_(
                CrossSourceNode.team_id == team_id,
                CrossSourceNode.is_deleted == "false"
            )
        ).order_by(CrossSourceNode.indexed_at.desc())

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        nodes = result.scalars().all()

        stats['total_nodes'] = len(nodes)

        logger.info(f"Processing {len(nodes)} nodes for link detection...")

        link_service = CrossSourceLinkService(db)

        for node in nodes:
            try:
                if not dry_run:
                    # Detect and create links
                    link_stats = await link_service.detect_and_create_links(
                        node=node,
                        team_id=team_id
                    )

                    stats['total_links_detected'] += link_stats['total_detected']
                    stats['total_edges_created'] += link_stats['created']
                else:
                    # Dry run - just count what we would detect
                    # (simplified - would need to actually run detection)
                    pass

                stats['processed'] += 1

                if stats['processed'] % 50 == 0:
                    logger.info(
                        f"Processed {stats['processed']}/{stats['total_nodes']} nodes, "
                        f"created {stats['total_edges_created']} edges..."
                    )

            except Exception as e:
                stats['failed'] += 1
                logger.error(
                    "link_detection_failed",
                    canonical_id=node.canonical_id,
                    error=str(e)
                )

        logger.info("detect_and_create_links_completed", stats=stats)
        return stats

    except Exception as e:
        logger.error("detect_and_create_links_failed", error=str(e))
        raise


async def main():
    parser = argparse.ArgumentParser(description="Backfill unified knowledge graph")
    parser.add_argument("--team-id", required=True, help="Team ID to migrate")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually write to database")
    parser.add_argument("--slack-only", action="store_true", help="Only migrate Slack messages")
    parser.add_argument("--github-only", action="store_true", help="Only migrate GitHub content")
    parser.add_argument("--no-links", action="store_true", help="Skip link detection")
    parser.add_argument("--limit", type=int, help="Limit number of items to migrate (for testing)")

    args = parser.parse_args()

    print("=" * 80)
    print("UNIFIED KNOWLEDGE GRAPH BACKFILL")
    print("=" * 80)
    print(f"Team ID: {args.team_id}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Limit: {args.limit or 'None'}")
    print("=" * 80)

    if args.dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be written to database\n")

    total_stats = {
        'slack': None,
        'github': None,
        'links': None
    }

    # Initialize database
    from app.core.database import db_manager
    db_manager.initialize()

    async for db in get_db():
        try:
            # Step 1: Migrate Slack messages
            if not args.github_only:
                print("\n📨 Step 1: Migrating Slack messages...")
                slack_stats = await backfill_slack_messages(
                    db=db,
                    team_id=args.team_id,
                    dry_run=args.dry_run,
                    limit=args.limit
                )
                total_stats['slack'] = slack_stats

                print(f"\nSlack Migration Results:")
                print(f"  Total messages: {slack_stats['total_messages']}")
                print(f"  Migrated: {slack_stats['migrated']}")
                print(f"  Already existed: {slack_stats['skipped_already_exists']}")
                print(f"  No content: {slack_stats['skipped_no_content']}")
                print(f"  Failed: {slack_stats['failed']}")

            # Step 2: Migrate GitHub content
            if not args.slack_only:
                print("\n🐙 Step 2: Migrating GitHub content...")
                github_stats = await backfill_github_content(
                    db=db,
                    team_id=args.team_id,
                    dry_run=args.dry_run,
                    limit=args.limit
                )
                total_stats['github'] = github_stats

                print(f"\nGitHub Migration Results:")
                print(f"  Total items: {github_stats['total_content']}")
                print(f"  Migrated: {github_stats['migrated']}")
                print(f"  Already existed: {github_stats['skipped_already_exists']}")
                print(f"  No content: {github_stats['skipped_no_content']}")
                print(f"  Failed: {github_stats['failed']}")

            # Step 3: Detect and create links
            if not args.no_links:
                print("\n🔗 Step 3: Detecting links and creating edges...")
                link_stats = await detect_and_create_links(
                    db=db,
                    team_id=args.team_id,
                    dry_run=args.dry_run,
                    limit=args.limit
                )
                total_stats['links'] = link_stats

                print(f"\nLink Detection Results:")
                print(f"  Nodes processed: {link_stats['processed']}")
                print(f"  Links detected: {link_stats['total_links_detected']}")
                print(f"  Edges created: {link_stats['total_edges_created']}")
                print(f"  Failed: {link_stats['failed']}")

            # Summary
            print("\n" + "=" * 80)
            print("MIGRATION COMPLETE!")
            print("=" * 80)

            total_migrated = 0
            if total_stats['slack']:
                total_migrated += total_stats['slack']['migrated']
            if total_stats['github']:
                total_migrated += total_stats['github']['migrated']

            print(f"\nTotal nodes migrated: {total_migrated}")
            if total_stats['links']:
                print(f"Total edges created: {total_stats['links']['total_edges_created']}")

            if args.dry_run:
                print("\n⚠️  This was a DRY RUN - no changes were made to the database")
                print("Run without --dry-run to actually perform the migration")
            else:
                print("\n✅ All changes have been committed to the database")

            print("\nNext steps:")
            print("  1. Verify data in cross_source_nodes table")
            print("  2. Verify data in cross_source_edges table")
            print("  3. Test cross-source search queries")
            print("  4. Check that embeddings are preserved (vector_id columns)")

        except Exception as e:
            print(f"\n❌ Migration failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            break


if __name__ == "__main__":
    asyncio.run(main())
