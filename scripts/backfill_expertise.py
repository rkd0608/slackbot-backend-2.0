"""Backfill expertise calculation for all users"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from app.core.database import db_manager
from app.models.user import User
from app.services.expertise_service import expertise_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def backfill_expertise(max_users: int = None):
    """Calculate expertise for all users"""

    logger.info("expertise_backfill_started", max_users=max_users)

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get all active users
            result = await db.execute(
                select(User.user_id)
                .where(User.is_bot == False)
            )
            user_ids = [row[0] for row in result.all()]

            logger.info("users_to_process", total=len(user_ids))

            if max_users:
                user_ids = user_ids[:max_users]

            processed = 0
            total_expertise_areas = 0

            for user_id in user_ids:
                try:
                    logger.info("processing_user", user_id=user_id, progress=f"{processed}/{len(user_ids)}")

                    # Calculate expertise
                    expertise_areas = await expertise_service.calculate_user_expertise(
                        user_id=user_id,
                        db=db
                    )

                    total_expertise_areas += len(expertise_areas)
                    processed += 1

                    if processed % 10 == 0:
                        logger.info(
                            "backfill_progress",
                            processed=processed,
                            total=len(user_ids),
                            percent=round(processed / len(user_ids) * 100, 2)
                        )

                except Exception as e:
                    logger.error(
                        "user_expertise_calculation_failed",
                        error=str(e),
                        user_id=user_id
                    )
                    continue

            logger.info(
                "expertise_backfill_completed",
                users_processed=processed,
                total_expertise_areas=total_expertise_areas,
                avg_areas_per_user=round(total_expertise_areas / processed, 2) if processed > 0 else 0
            )

            # Print summary
            from app.models.user_expertise import UserExpertise
            from app.models.entity import Entity

            result = await db.execute(select(func.count(UserExpertise.id)))
            expertise_count = result.scalar()

            # Get top experts
            result = await db.execute(
                select(UserExpertise, Entity)
                .join(Entity, UserExpertise.entity_id == Entity.id)
                .order_by(UserExpertise.expertise_score.desc())
                .limit(20)
            )
            top_experts = result.all()

            print("\n" + "="*80)
            print("EXPERTISE CALCULATION SUMMARY")
            print("="*80)
            print(f"Users processed: {processed}")
            print(f"Total expertise records: {expertise_count}")
            print(f"\nTop 20 Expert-Topic Pairs:")
            print("-"*80)
            for i, (exp, entity) in enumerate(top_experts, 1):
                print(f"{i:2}. {exp.user_id:30} - {entity.canonical_form:30} (score: {exp.expertise_score:.4f})")
            print("="*80)

            break  # Only use first session

    except Exception as e:
        logger.error("backfill_failed", error=str(e))
        raise

    finally:
        await db_manager.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill expertise calculation")
    parser.add_argument(
        "--max-users",
        type=int,
        default=None,
        help="Maximum number of users to process (for testing)"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt"
    )

    args = parser.parse_args()

    if not args.confirm:
        print("\nThis will calculate expertise for all users.")
        print("This may take a while depending on user and message count.")
        print("It will also incur OpenAI API costs if LLM extraction is enabled.")
        confirm = input("\nContinue? (y/n): ")

        if confirm.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    asyncio.run(backfill_expertise(max_users=args.max_users))
