"""Re-process ALL messages to find code with improved CodeDetector"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func, delete
from app.core.database import db_manager
from app.models.message import Message
from app.models.code_snippet import CodeSnippet
from app.services.code_detector import code_detector
from app.services.code_extraction_service import code_extraction_service
from app.services.code_embedding_service import code_embedding_service
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def reprocess_all_messages(batch_size: int = 50, max_messages: int = None, force_reprocess: bool = False):
    """Re-process ALL messages to find code with improved CodeDetector"""

    if not settings.enable_code_intelligence:
        print("\nERROR: Code intelligence is disabled in settings.")
        return

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get total message count
            result = await db.execute(select(func.count(Message.id)))
            total_count = result.scalar()

            if max_messages:
                total_count = min(total_count, max_messages)

            print(f"\n{'='*80}")
            print(f"RE-PROCESSING ALL MESSAGES FOR CODE")
            print(f"{'='*80}")
            print(f"Total messages to scan: {total_count}")
            print(f"Force reprocess: {force_reprocess}")
            print(f"{'='*80}\n")

            # Counters
            processed = 0
            new_code_found = 0
            snippets_created = 0
            snippets_embedded = 0
            messages_updated = 0
            failed = 0

            # Get all messages ordered by timestamp
            query = select(Message).order_by(Message.timestamp.desc())
            if max_messages:
                query = query.limit(max_messages)

            result = await db.execute(query)
            messages = result.scalars().all()

            # Eagerly load message data to avoid lazy loading issues
            message_data = [
                {
                    "id": msg.id,
                    "message_id": msg.message_id,
                    "text": msg.text,
                    "has_code": msg.has_code
                }
                for msg in messages
            ]

            for i in range(0, len(message_data), batch_size):
                batch = message_data[i:i + batch_size]

                for msg_data in batch:
                    try:
                        # Use CodeDetector to detect code
                        code_blocks = code_detector.detect_code_blocks(msg_data["text"])
                        has_code_now = len(code_blocks) > 0

                        # Update has_code flag if it changed
                        if has_code_now != msg_data["has_code"]:
                            # Fetch and update the message
                            result = await db.execute(
                                select(Message).where(Message.message_id == msg_data["message_id"])
                            )
                            message = result.scalar_one()

                            message.has_code = has_code_now
                            if has_code_now:
                                # Extract languages
                                languages = list(set([
                                    cb.get("language")
                                    for cb in code_blocks
                                    if cb.get("language") and cb.get("language") != "unknown"
                                ]))
                                message.code_languages = languages if languages else None
                                messages_updated += 1
                                new_code_found += 1

                        # If has code, extract and embed
                        if has_code_now:
                            # Check existing snippets
                            result = await db.execute(
                                select(func.count(CodeSnippet.id))
                                .where(CodeSnippet.message_id == msg_data["message_id"])
                            )
                            existing_count = result.scalar()

                            # If force reprocess, delete old snippets
                            if force_reprocess and existing_count > 0:
                                await db.execute(
                                    delete(CodeSnippet)
                                    .where(CodeSnippet.message_id == msg_data["message_id"])
                                )
                                logger.info(
                                    "deleted_old_snippets",
                                    message_id=msg_data["message_id"],
                                    count=existing_count
                                )
                                existing_count = 0

                            # Extract if no existing snippets
                            if existing_count == 0:
                                snippet_ids = await code_extraction_service.extract_and_store_code(
                                    message_id=msg_data["message_id"],
                                    text=msg_data["text"],
                                    db=db
                                )

                                snippets_created += len(snippet_ids)

                                # Embed each snippet (with rate limit handling)
                                for snippet_id in snippet_ids:
                                    try:
                                        success = await code_embedding_service.embed_code_snippet(
                                            snippet_id=snippet_id,
                                            db=db
                                        )
                                        if success:
                                            snippets_embedded += 1
                                    except Exception as e:
                                        logger.error("embedding_failed", snippet_id=snippet_id, error=str(e))
                                        failed += 1
                                        # Continue even if embedding fails (rate limit)
                                        continue

                        processed += 1

                        if processed % 50 == 0:
                            print(f"Progress: {processed}/{total_count} ({round(processed/total_count*100, 1)}%) | New code: {new_code_found} | Snippets: {snippets_created} | Embedded: {snippets_embedded}")

                    except Exception as e:
                        logger.error("message_processing_error", message_id=msg_data.get("message_id"), error=str(e))
                        failed += 1
                        continue

                # Commit batch
                await db.commit()

            # Final stats
            print(f"\n{'='*80}")
            print("RE-PROCESSING COMPLETE")
            print(f"{'='*80}")
            print(f"Messages scanned: {processed}")
            print(f"New code found: {new_code_found}")
            print(f"Messages updated (has_code flag): {messages_updated}")
            print(f"Code snippets created: {snippets_created}")
            print(f"Snippets embedded: {snippets_embedded}")
            print(f"Failed: {failed}")

            # Get final counts
            result = await db.execute(select(func.count(Message.id)).where(Message.has_code == True))
            total_with_code = result.scalar()

            result = await db.execute(select(func.count(CodeSnippet.id)))
            total_snippets = result.scalar()

            print(f"\nFinal Database State:")
            print(f"  Messages with code: {total_with_code}")
            print(f"  Total code snippets: {total_snippets}")

            # Pinecone stats
            try:
                from app.core.vector_db import vector_db_manager
                if vector_db_manager.index is None:
                    vector_db_manager.initialize()
                namespace_stats = vector_db_manager.get_namespace_stats(settings.pinecone_code_namespace)
                vector_count = namespace_stats.get("vector_count", 0)
                print(f"  Pinecone 'code' namespace: {vector_count} vectors")
            except Exception as e:
                logger.warning("pinecone_stats_error", error=str(e))

            print(f"{'='*80}\n")

            break

    except Exception as e:
        logger.error("reprocessing_failed", error=str(e))
        raise

    finally:
        await db_manager.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Re-process all messages for code")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-messages", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Force reprocess (delete existing snippets)")
    parser.add_argument("--confirm", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if not args.confirm:
        print(f"\n{'='*80}")
        print("RE-PROCESS ALL MESSAGES FOR CODE")
        print(f"{'='*80}")
        print("\nThis will:")
        print("1. Scan ALL messages (not just has_code=true)")
        print("2. Use improved CodeDetector (handles more formats)")
        print("3. Update has_code flags where code was missed")
        print("4. Extract and embed newly found code snippets")
        if args.force:
            print("5. DELETE and re-extract existing snippets (--force)")
        print(f"\nBatch size: {args.batch_size}")
        if args.max_messages:
            print(f"Max messages: {args.max_messages} (testing)")
        print(f"{'='*80}")

        confirm = input("\nContinue? (y/n): ")
        if confirm.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    asyncio.run(reprocess_all_messages(
        batch_size=args.batch_size,
        max_messages=args.max_messages,
        force_reprocess=args.force
    ))
