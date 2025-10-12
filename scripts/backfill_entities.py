"""Backfill entity extraction for existing messages"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from app.core.database import db_manager
from app.models.message import Message
from app.services.entity_service import entity_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def backfill_entities(batch_size: int = 100, max_messages: int = None):
    """Extract entities from all existing messages"""

    logger.info("entity_backfill_started", batch_size=batch_size, max_messages=max_messages)

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get total message count
            result = await db.execute(select(func.count(Message.id)))
            total_count = result.scalar()

            logger.info("messages_to_process", total=total_count)

            if max_messages:
                total_count = min(total_count, max_messages)

            # Process messages in batches
            processed = 0
            entities_extracted = 0

            # Get messages ordered by timestamp (newest first for more relevant entities)
            query = (
                select(Message)
                .where(Message.text_processed.isnot(None))
                .order_by(Message.timestamp.desc())
            )

            if max_messages:
                query = query.limit(max_messages)

            result = await db.execute(query)
            messages = result.scalars().all()

            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]

                for message in batch:
                    try:
                        # Skip very short messages
                        if not message.text_processed or len(message.text_processed) < 10:
                            continue

                        # Extract entities
                        entities = await entity_service.extract_and_store_entities(
                            message_text=message.text_processed,
                            message_id=message.message_id,
                            channel_id=message.channel_id,
                            user_id=message.user_id,
                            db=db
                        )

                        entities_extracted += len(entities)
                        processed += 1

                        if processed % 50 == 0:
                            logger.info(
                                "backfill_progress",
                                processed=processed,
                                total=total_count,
                                entities=entities_extracted,
                                percent=round(processed / total_count * 100, 2)
                            )

                    except Exception as e:
                        logger.error(
                            "message_entity_extraction_failed",
                            error=str(e),
                            message_id=message.message_id
                        )
                        continue

                # Commit batch
                await db.commit()

                logger.info(
                    "batch_completed",
                    batch_num=i // batch_size + 1,
                    processed=processed,
                    entities=entities_extracted
                )

            logger.info(
                "entity_backfill_completed",
                messages_processed=processed,
                entities_extracted=entities_extracted,
                avg_entities_per_message=round(entities_extracted / processed, 2) if processed > 0 else 0
            )

            # Print entity statistics
            from app.models.entity import Entity, EntityRelationship

            result = await db.execute(select(func.count(Entity.id)))
            entity_count = result.scalar()

            result = await db.execute(select(func.count(EntityRelationship.id)))
            relationship_count = result.scalar()

            # Get top entities
            result = await db.execute(
                select(Entity)
                .order_by(Entity.occurrence_count.desc())
                .limit(20)
            )
            top_entities = result.scalars().all()

            print("\n" + "="*80)
            print("ENTITY EXTRACTION SUMMARY")
            print("="*80)
            print(f"Messages processed: {processed}")
            print(f"Total unique entities: {entity_count}")
            print(f"Total relationships: {relationship_count}")
            print(f"\nTop 20 Most Frequent Entities:")
            print("-"*80)
            for i, entity in enumerate(top_entities, 1):
                print(f"{i:2}. {entity.canonical_form:30} ({entity.entity_type:12}) - {entity.occurrence_count:4} occurrences in {entity.channel_count:2} channels")
            print("="*80)

            break  # Only use first session

    except Exception as e:
        logger.error("backfill_failed", error=str(e))
        raise

    finally:
        await db_manager.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill entity extraction")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of messages to process in each batch"
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Maximum number of messages to process (for testing)"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt"
    )

    args = parser.parse_args()

    if not args.confirm:
        print("\nThis will extract entities from existing messages.")
        print("This may take a while depending on your message count.")
        print("It will also incur OpenAI API costs if LLM extraction is enabled.")
        confirm = input("\nContinue? (y/n): ")

        if confirm.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    asyncio.run(backfill_entities(
        batch_size=args.batch_size,
        max_messages=args.max_messages
    ))
