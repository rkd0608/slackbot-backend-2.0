"""Backfill code snippet extraction for existing messages with code"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from app.core.database import db_manager
from app.models.message import Message
from app.models.code_snippet import CodeSnippet
from app.services.code_extraction_service import code_extraction_service
from app.services.code_embedding_service import code_embedding_service
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def backfill_code_snippets(batch_size: int = 50, max_messages: int = None):
    """Extract code snippets from all existing messages with code"""

    if not settings.enable_code_intelligence:
        logger.error("code_intelligence_disabled", message="Code intelligence is disabled in settings")
        print("\nERROR: Code intelligence is disabled in settings.")
        print("Please set ENABLE_CODE_INTELLIGENCE=true in your .env file")
        return

    logger.info("code_backfill_started", batch_size=batch_size, max_messages=max_messages)

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get total message count with code
            result = await db.execute(
                select(func.count(Message.id)).where(Message.has_code == True)
            )
            total_count = result.scalar()

            logger.info("messages_with_code", total=total_count)

            if total_count == 0:
                print("\nNo messages with code found. Nothing to backfill.")
                return

            if max_messages:
                total_count = min(total_count, max_messages)

            # Process messages in batches
            processed = 0
            snippets_extracted = 0
            snippets_embedded = 0
            failed = 0

            # Get messages with code ordered by timestamp (newest first)
            query = (
                select(Message)
                .where(Message.has_code == True)
                .order_by(Message.timestamp.desc())
            )

            if max_messages:
                query = query.limit(max_messages)

            result = await db.execute(query)
            messages = result.scalars().all()

            print(f"\nProcessing {len(messages)} messages with code...")
            print("="*80)

            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]

                for message in batch:
                    try:
                        # Check if code snippets already extracted for this message
                        result = await db.execute(
                            select(func.count(CodeSnippet.id))
                            .where(CodeSnippet.message_id == message.message_id)
                        )
                        existing_count = result.scalar()

                        if existing_count > 0:
                            logger.info(
                                "code_already_extracted",
                                message_id=message.message_id,
                                snippet_count=existing_count
                            )
                            processed += 1
                            snippets_extracted += existing_count
                            continue

                        # Extract and store code snippets
                        snippet_ids = await code_extraction_service.extract_and_store_code(
                            message_id=message.message_id,
                            text=message.text,
                            db=db
                        )

                        snippets_extracted += len(snippet_ids)

                        # Generate embeddings for each snippet
                        for snippet_id in snippet_ids:
                            try:
                                success = await code_embedding_service.embed_code_snippet(
                                    snippet_id=snippet_id,
                                    db=db
                                )
                                if success:
                                    snippets_embedded += 1
                                else:
                                    logger.warning("code_embedding_failed", snippet_id=snippet_id)
                                    failed += 1
                            except Exception as embed_error:
                                logger.error(
                                    "code_embedding_error",
                                    snippet_id=snippet_id,
                                    error=str(embed_error)
                                )
                                failed += 1

                        processed += 1

                        if processed % 10 == 0:
                            logger.info(
                                "backfill_progress",
                                processed=processed,
                                total=total_count,
                                snippets=snippets_extracted,
                                embedded=snippets_embedded,
                                percent=round(processed / total_count * 100, 2)
                            )
                            print(f"Progress: {processed}/{total_count} messages ({round(processed/total_count*100, 1)}%) | Snippets: {snippets_extracted} | Embedded: {snippets_embedded}")

                    except Exception as e:
                        logger.error(
                            "message_code_extraction_failed",
                            error=str(e),
                            message_id=message.message_id
                        )
                        failed += 1
                        continue

                # Commit batch
                await db.commit()

                logger.info(
                    "batch_completed",
                    batch_num=i // batch_size + 1,
                    processed=processed,
                    snippets=snippets_extracted,
                    embedded=snippets_embedded
                )

            logger.info(
                "code_backfill_completed",
                messages_processed=processed,
                snippets_extracted=snippets_extracted,
                snippets_embedded=snippets_embedded,
                failed=failed,
                avg_snippets_per_message=round(snippets_extracted / processed, 2) if processed > 0 else 0
            )

            # Print code snippet statistics
            result = await db.execute(select(func.count(CodeSnippet.id)))
            snippet_count = result.scalar()

            # Get language distribution
            result = await db.execute(
                select(
                    CodeSnippet.language,
                    func.count(CodeSnippet.id).label('count')
                )
                .where(CodeSnippet.language.isnot(None))
                .group_by(CodeSnippet.language)
                .order_by(func.count(CodeSnippet.id).desc())
            )
            language_stats = result.all()

            # Get code type distribution
            result = await db.execute(
                select(
                    CodeSnippet.code_type,
                    func.count(CodeSnippet.id).label('count')
                )
                .where(CodeSnippet.code_type.isnot(None))
                .group_by(CodeSnippet.code_type)
                .order_by(func.count(CodeSnippet.id).desc())
            )
            type_stats = result.all()

            # Get embedding statistics
            result = await db.execute(
                select(func.count(CodeSnippet.id))
                .where(CodeSnippet.vector_id.isnot(None))
            )
            embedded_count = result.scalar()

            print("\n" + "="*80)
            print("CODE SNIPPET EXTRACTION SUMMARY")
            print("="*80)
            print(f"Messages processed: {processed}")
            print(f"Total code snippets extracted: {snippets_extracted}")
            print(f"Successfully embedded: {snippets_embedded}")
            print(f"Failed: {failed}")
            print(f"Total snippets in database: {snippet_count}")
            print(f"Total embedded snippets: {embedded_count}")

            print(f"\nLanguage Distribution:")
            print("-"*80)
            for lang, count in language_stats:
                print(f"  {lang:20} {count:5} snippets")

            print(f"\nCode Type Distribution:")
            print("-"*80)
            for code_type, count in type_stats:
                print(f"  {code_type:20} {count:5} snippets")

            print("="*80)

            # Verify Pinecone namespace stats
            try:
                from app.core.vector_db import vector_db_manager
                # Initialize vector_db_manager if not already initialized
                if vector_db_manager.index is None:
                    vector_db_manager.initialize()
                namespace_stats = vector_db_manager.get_namespace_stats(settings.pinecone_code_namespace)
                vector_count = namespace_stats.get("vector_count", 0)
                print(f"\nPinecone 'code' namespace: {vector_count} vectors")
            except Exception as e:
                logger.warning("pinecone_stats_error", error=str(e))

            print("="*80)

            break  # Only use first session

    except Exception as e:
        logger.error("backfill_failed", error=str(e))
        raise

    finally:
        await db_manager.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill code snippet extraction")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
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
        print("\n" + "="*80)
        print("CODE SNIPPET BACKFILL")
        print("="*80)
        print("\nThis script will:")
        print("1. Find all messages with code blocks")
        print("2. Extract code snippets using AST parsing (where applicable)")
        print("3. Generate embeddings using Voyage AI or OpenAI")
        print("4. Store embeddings in Pinecone 'code' namespace")
        print("\nIMPORTANT:")
        print("- This may take a while depending on your message count")
        print(f"- Embedding provider: {settings.code_embedding_provider}")
        print(f"- Model: {settings.code_embedding_model}")

        if settings.code_embedding_provider == "voyage":
            print("- Cost: ~$0.12 per 1M tokens (Voyage AI Code-2)")
        else:
            print("- Cost: OpenAI embedding costs will apply")

        print(f"- Batch size: {args.batch_size} messages")
        if args.max_messages:
            print(f"- Limited to: {args.max_messages} messages (testing mode)")

        print("="*80)
        confirm = input("\nContinue? (y/n): ")

        if confirm.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    asyncio.run(backfill_code_snippets(
        batch_size=args.batch_size,
        max_messages=args.max_messages
    ))
