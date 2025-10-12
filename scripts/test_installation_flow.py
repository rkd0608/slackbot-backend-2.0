"""Test installation flow with knowledge graph building"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from app.core.database import db_manager
from app.models.workspace import Workspace
from app.models.entity import Entity, EntityRelationship
from app.models.user_expertise import UserExpertise, SignificantEvent
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_installation_knowledge_graph():
    """Test that knowledge graph is built during installation"""

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get the test workspace
            result = await db.execute(
                select(Workspace).where(Workspace.team_id == "T0420EE1VQ8")
            )
            workspace = result.scalar_one_or_none()

            if not workspace:
                print("❌ Test workspace not found")
                return

            print("\n" + "="*80)
            print("INSTALLATION KNOWLEDGE GRAPH TEST")
            print("="*80)
            print(f"\nWorkspace: {workspace.team_name}")
            print(f"Team ID: {workspace.team_id}")
            print(f"Indexing Status: {workspace.indexing_status}")
            print(f"Messages Indexed: {workspace.total_messages_indexed}")
            print(f"Channels Indexed: {workspace.total_channels_indexed}")

            # Check entities
            result = await db.execute(
                select(func.count(Entity.id))
            )
            entity_count = result.scalar()
            print(f"\n✓ Entities: {entity_count}")

            # Check entity relationships
            result = await db.execute(
                select(func.count(EntityRelationship.id))
            )
            relationship_count = result.scalar()
            print(f"✓ Entity Relationships: {relationship_count}")

            # Check user expertise
            result = await db.execute(
                select(func.count(UserExpertise.id))
            )
            expertise_count = result.scalar()
            print(f"✓ User Expertise Records: {expertise_count}")

            # Check significant events
            result = await db.execute(
                select(func.count(SignificantEvent.id))
            )
            event_count = result.scalar()
            print(f"✓ Significant Events: {event_count}")

            # Show sample relationships
            if relationship_count > 0:
                print("\n" + "-"*80)
                print("Sample Entity Relationships:")
                print("-"*80)

                result = await db.execute(
                    select(EntityRelationship)
                    .order_by(EntityRelationship.confidence_score.desc())
                    .limit(10)
                )
                relationships = result.scalars().all()

                for rel in relationships:
                    # Get entity names
                    e1_result = await db.execute(select(Entity).where(Entity.id == rel.entity_id_1))
                    e1 = e1_result.scalar_one_or_none()

                    e2_result = await db.execute(select(Entity).where(Entity.id == rel.entity_id_2))
                    e2 = e2_result.scalar_one_or_none()

                    if e1 and e2:
                        print(f"  {e1.canonical_form} ↔ {e2.canonical_form} (confidence: {rel.confidence_score:.4f}, co-occurrences: {rel.co_occurrence_count})")

            # Show sample expertise
            if expertise_count > 0:
                print("\n" + "-"*80)
                print("Top 10 User Expertise Areas:")
                print("-"*80)

                result = await db.execute(
                    select(UserExpertise)
                    .order_by(UserExpertise.expertise_score.desc())
                    .limit(10)
                )
                expertise_records = result.scalars().all()

                for exp in expertise_records:
                    # Get entity name
                    entity_result = await db.execute(
                        select(Entity).where(Entity.id == exp.entity_id)
                    )
                    entity = entity_result.scalar_one_or_none()

                    if entity:
                        print(f"  {exp.user_id[:20]:20} → {entity.canonical_form[:30]:30} (score: {exp.expertise_score:.4f})")

            # Show sample events
            if event_count > 0:
                print("\n" + "-"*80)
                print("Recent Significant Events:")
                print("-"*80)

                result = await db.execute(
                    select(SignificantEvent)
                    .order_by(SignificantEvent.start_time.desc())
                    .limit(5)
                )
                events = result.scalars().all()

                for event in events:
                    print(f"  [{event.event_type}] {event.event_name}")
                    if event.event_description:
                        print(f"    {event.event_description[:100]}")

            print("\n" + "="*80)
            print("KNOWLEDGE GRAPH VALIDATION")
            print("="*80)

            validation_results = []

            # Validate entities exist
            if entity_count > 0:
                validation_results.append(("✓", "Entities extracted"))
            else:
                validation_results.append(("✗", "No entities found"))

            # Validate relationships exist
            if relationship_count > 0:
                validation_results.append(("✓", "Entity relationships built"))
            else:
                validation_results.append(("✗", "No relationships found"))

            # Validate expertise calculated
            if expertise_count > 0:
                validation_results.append(("✓", "User expertise calculated"))
            else:
                validation_results.append(("✗", "No expertise records found"))

            # Validate events detected
            if event_count > 0:
                validation_results.append(("✓", "Significant events detected"))
            else:
                validation_results.append(("⚠", "No significant events (may be normal for new workspace)"))

            for symbol, message in validation_results:
                print(f"{symbol} {message}")

            # Overall status
            print("\n" + "="*80)
            critical_checks = [entity_count > 0, relationship_count > 0, expertise_count > 0]

            if all(critical_checks):
                print("✅ KNOWLEDGE GRAPH READY - All critical components built successfully!")
            elif any(critical_checks):
                print("⚠️  PARTIAL SUCCESS - Some knowledge graph components missing")
            else:
                print("❌ KNOWLEDGE GRAPH NOT BUILT - Installation may have failed")

            print("="*80 + "\n")

            break  # Only use first session

    except Exception as e:
        logger.error("test_installation_error", error=str(e))
        print(f"\n❌ Test failed: {e}")
        raise

    finally:
        await db_manager.close()


if __name__ == "__main__":
    print("\nTesting knowledge graph build during installation...\n")
    asyncio.run(test_installation_knowledge_graph())
