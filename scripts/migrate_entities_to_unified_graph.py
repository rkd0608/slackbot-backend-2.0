"""
Migrate Entity and EntityRelationship data to unified Cross-Source Knowledge Graph

This script:
1. Reads existing entities from the Entity table
2. Creates CrossSourceNode records for each entity
3. Reads EntityRelationship records
4. Creates CrossSourceEdge records for relationships
5. Validates the migration
6. Optionally creates MENTIONS edges linking content to entities

Run with: python -m scripts.migrate_entities_to_unified_graph
"""
import asyncio
import sys
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.models.entity import Entity, EntityRelationship
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType, DetectionMethod
from app.core.logging import get_logger
import uuid

logger = get_logger(__name__)


class EntityMigrator:
    """Migrates legacy entity data to unified knowledge graph"""

    def __init__(self, db: AsyncSession, dry_run: bool = False):
        self.db = db
        self.dry_run = dry_run
        self.stats = {
            "entities_processed": 0,
            "nodes_created": 0,
            "relationships_processed": 0,
            "edges_created": 0,
            "errors": []
        }

    def _get_node_type_from_entity_type(self, entity_type: str) -> NodeType:
        """Map old entity types to new NodeType enum"""
        mapping = {
            "PERSON": NodeType.PERSON,
            "ORGANIZATION": NodeType.ORGANIZATION,
            "PROJECT": NodeType.PROJECT,
            "TECHNOLOGY": NodeType.TECHNOLOGY,
            "TOPIC": NodeType.TOPIC,
            "LOCATION": NodeType.LOCATION,
            "EVENT": NodeType.EVENT,
        }
        return mapping.get(entity_type, NodeType.TOPIC)  # Default to TOPIC

    async def migrate_entities(self, team_id: str = None):
        """
        Migrate Entity records to CrossSourceNode

        Args:
            team_id: Optional team_id to migrate (if None, migrates all)
        """
        try:
            logger.info("entity_migration_started", team_id=team_id, dry_run=self.dry_run)

            # Query entities
            query = select(Entity)
            if team_id:
                query = query.where(Entity.team_id == team_id)

            result = await self.db.execute(query)
            entities = result.scalars().all()

            logger.info("entities_found", count=len(entities))

            for entity in entities:
                try:
                    self.stats["entities_processed"] += 1

                    # Create canonical ID for entity node
                    canonical_id = f"entity:{entity.entity_type.lower()}:{entity.canonical_form}"

                    # Check if node already exists
                    existing = await self.db.execute(
                        select(CrossSourceNode).where(
                            CrossSourceNode.canonical_id == canonical_id
                        )
                    )
                    if existing.scalar_one_or_none():
                        logger.info("entity_node_exists", canonical_id=canonical_id)
                        continue

                    # Determine node type
                    node_type = self._get_node_type_from_entity_type(entity.entity_type)

                    # Build entity_metadata from Entity fields
                    entity_metadata = {
                        "canonical_form": entity.canonical_form,
                        "variations": getattr(entity, 'variations', None) or [],
                        "occurrence_count": entity.occurrence_count,
                        "entity_type": entity.entity_type,
                        "channels": entity.related_channels or [],
                        "first_seen": entity.first_seen_at.isoformat() if entity.first_seen_at else None,
                        "last_seen": entity.last_seen_at.isoformat() if entity.last_seen_at else None,
                        "migrated_from_entity_id": entity.id,
                        "migration_timestamp": datetime.utcnow().isoformat()
                    }

                    # Create CrossSourceNode
                    node = CrossSourceNode(
                        canonical_id=canonical_id,
                        team_id=entity.team_id,
                        source="derived",  # Entities are derived/extracted
                        source_id=f"entity_{entity.id}",
                        node_type=node_type,
                        title=entity.entity_text,
                        content=f"{entity.entity_type}: {entity.entity_text}",
                        summary=f"Extracted entity appearing {entity.occurrence_count} times across {entity.channel_count} channels",
                        url="",  # Entities don't have URLs
                        visibility="team",
                        author=None,
                        author_id=None,
                        created_at=entity.first_seen_at or datetime.utcnow(),
                        updated_at=entity.last_seen_at,
                        entity_metadata=entity_metadata,
                        importance_score={
                            "occurrence_count": entity.occurrence_count,
                            "channel_count": entity.channel_count,
                            "computed_at": datetime.utcnow().isoformat()
                        }
                    )

                    if not self.dry_run:
                        self.db.add(node)

                    self.stats["nodes_created"] += 1

                    logger.info(
                        "entity_node_created",
                        canonical_id=canonical_id,
                        entity_text=entity.entity_text,
                        entity_type=entity.entity_type
                    )

                except Exception as e:
                    error_msg = f"Error migrating entity {entity.id}: {str(e)}"
                    logger.error("entity_migration_error", entity_id=entity.id, error=str(e))
                    self.stats["errors"].append(error_msg)

            if not self.dry_run:
                await self.db.commit()

            logger.info("entities_migrated", stats=self.stats)

        except Exception as e:
            logger.error("entity_migration_failed", error=str(e))
            await self.db.rollback()
            raise

    async def migrate_relationships(self, team_id: str = None):
        """
        Migrate EntityRelationship records to CrossSourceEdge

        Args:
            team_id: Optional team_id to migrate (if None, migrates all)
        """
        try:
            logger.info("relationship_migration_started", team_id=team_id, dry_run=self.dry_run)

            # Query relationships
            query = select(EntityRelationship)
            if team_id:
                query = query.where(EntityRelationship.team_id == team_id)

            result = await self.db.execute(query)
            relationships = result.scalars().all()

            logger.info("relationships_found", count=len(relationships))

            for rel in relationships:
                try:
                    self.stats["relationships_processed"] += 1

                    # Get the source and target entities
                    entity1_result = await self.db.execute(
                        select(Entity).where(Entity.id == rel.entity_id_1)
                    )
                    entity1 = entity1_result.scalar_one_or_none()

                    entity2_result = await self.db.execute(
                        select(Entity).where(Entity.id == rel.entity_id_2)
                    )
                    entity2 = entity2_result.scalar_one_or_none()

                    if not entity1 or not entity2:
                        logger.warning("missing_entities_for_relationship", relationship_id=rel.id)
                        continue

                    # Build canonical IDs
                    source_canonical_id = f"entity:{entity1.entity_type.lower()}:{entity1.canonical_form}"
                    target_canonical_id = f"entity:{entity2.entity_type.lower()}:{entity2.canonical_form}"

                    # Check if edge already exists
                    existing_edge = await self.db.execute(
                        select(CrossSourceEdge).where(
                            CrossSourceEdge.source_node_id == source_canonical_id,
                            CrossSourceEdge.target_node_id == target_canonical_id,
                            CrossSourceEdge.edge_type == EdgeType.CO_OCCURS_WITH
                        )
                    )
                    if existing_edge.scalar_one_or_none():
                        logger.info("edge_exists", source=source_canonical_id, target=target_canonical_id)
                        continue

                    # Determine edge type from relationship_type
                    edge_type = EdgeType.CO_OCCURS_WITH  # Default
                    if rel.relationship_type == "works_with":
                        edge_type = EdgeType.WORKS_WITH
                    elif rel.relationship_type == "related_to":
                        edge_type = EdgeType.RELATED_TO

                    # Create edge
                    edge = CrossSourceEdge(
                        id=str(uuid.uuid4()),
                        source_node_id=source_canonical_id,
                        target_node_id=target_canonical_id,
                        edge_type=edge_type,
                        team_id=rel.team_id,
                        confidence=rel.confidence_score,
                        detection_method=DetectionMethod.CO_OCCURRENCE,
                        evidence=f"Co-occurred {rel.co_occurrence_count} times in Slack messages",
                        metadata={
                            "co_occurrence_count": rel.co_occurrence_count,
                            "channels": rel.channels or [],
                            "first_seen": rel.first_seen_at.isoformat() if rel.first_seen_at else None,
                            "last_seen": rel.last_seen_at.isoformat() if rel.last_seen_at else None,
                            "migrated_from_relationship_id": rel.id,
                            "migration_timestamp": datetime.utcnow().isoformat()
                        },
                        created_at=rel.first_seen_at or datetime.utcnow(),
                        updated_at=rel.last_seen_at
                    )

                    if not self.dry_run:
                        self.db.add(edge)

                    self.stats["edges_created"] += 1

                    logger.info(
                        "relationship_edge_created",
                        source=entity1.entity_text,
                        target=entity2.entity_text,
                        edge_type=edge_type.value
                    )

                except Exception as e:
                    error_msg = f"Error migrating relationship {rel.id}: {str(e)}"
                    logger.error("relationship_migration_error", relationship_id=rel.id, error=str(e))
                    self.stats["errors"].append(error_msg)

            if not self.dry_run:
                await self.db.commit()

            logger.info("relationships_migrated", stats=self.stats)

        except Exception as e:
            logger.error("relationship_migration_failed", error=str(e))
            await self.db.rollback()
            raise

    async def validate_migration(self, team_id: str = None):
        """Validate that migration was successful"""
        try:
            logger.info("validation_started", team_id=team_id)

            # Count entities
            entity_query = select(func.count(Entity.id))
            if team_id:
                entity_query = entity_query.where(Entity.team_id == team_id)
            entity_result = await self.db.execute(entity_query)
            entity_count = entity_result.scalar()

            # Count entity nodes (derived source)
            node_query = select(func.count(CrossSourceNode.canonical_id)).where(
                CrossSourceNode.source == "derived"
            )
            if team_id:
                node_query = node_query.where(CrossSourceNode.team_id == team_id)
            node_result = await self.db.execute(node_query)
            node_count = node_result.scalar()

            # Count relationships
            rel_query = select(func.count(EntityRelationship.id))
            if team_id:
                rel_query = rel_query.where(EntityRelationship.team_id == team_id)
            rel_result = await self.db.execute(rel_query)
            rel_count = rel_result.scalar()

            # Count co_occurs_with edges
            edge_query = select(func.count(CrossSourceEdge.id)).where(
                CrossSourceEdge.edge_type == EdgeType.CO_OCCURS_WITH
            )
            if team_id:
                edge_query = edge_query.where(CrossSourceEdge.team_id == team_id)
            edge_result = await self.db.execute(edge_query)
            edge_count = edge_result.scalar()

            validation = {
                "entity_count": entity_count,
                "entity_node_count": node_count,
                "relationship_count": rel_count,
                "edge_count": edge_count,
                "entities_migrated": node_count >= entity_count,
                "relationships_migrated": edge_count >= rel_count
            }

            logger.info("validation_results", **validation)

            return validation

        except Exception as e:
            logger.error("validation_failed", error=str(e))
            raise


async def main():
    """Main migration function"""
    import argparse
    from app.core.database import db_manager

    parser = argparse.ArgumentParser(description="Migrate entities to unified knowledge graph")
    parser.add_argument("--team-id", help="Migrate specific team only")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (don't commit changes)")
    parser.add_argument("--validate-only", action="store_true", help="Only run validation")

    args = parser.parse_args()

    # Initialize database manager
    db_manager.initialize()

    async for db in get_db():
        try:
            migrator = EntityMigrator(db, dry_run=args.dry_run)

            if args.validate_only:
                # Just validate
                validation = await migrator.validate_migration(args.team_id)
                print("\n=== Validation Results ===")
                for key, value in validation.items():
                    print(f"{key}: {value}")
                return

            # Run migration
            print(f"\n=== Starting Entity Migration ===")
            print(f"Team ID: {args.team_id or 'ALL'}")
            print(f"Dry Run: {args.dry_run}")
            print()

            # Migrate entities
            await migrator.migrate_entities(args.team_id)

            # Migrate relationships
            await migrator.migrate_relationships(args.team_id)

            # Validate
            validation = await migrator.validate_migration(args.team_id)

            print("\n=== Migration Complete ===")
            print(f"Entities processed: {migrator.stats['entities_processed']}")
            print(f"Nodes created: {migrator.stats['nodes_created']}")
            print(f"Relationships processed: {migrator.stats['relationships_processed']}")
            print(f"Edges created: {migrator.stats['edges_created']}")
            print(f"Errors: {len(migrator.stats['errors'])}")

            if migrator.stats['errors']:
                print("\nErrors:")
                for error in migrator.stats['errors'][:10]:  # Show first 10
                    print(f"  - {error}")

            print("\n=== Validation ===")
            for key, value in validation.items():
                print(f"{key}: {value}")

            if not args.dry_run:
                print("\n✅ Migration committed to database")
            else:
                print("\n⚠️  Dry run - no changes committed")

        except Exception as e:
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
