"""Entity extraction and knowledge graph service"""
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_, func
from openai import AsyncOpenAI
import json
import re

from app.models.entity import Entity, EntityRelationship
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EntityService:
    """Manages entity extraction, storage, and knowledge graph building"""

    # Entity types
    TYPE_TECHNICAL = "technical"  # postgres, k8s, API, database
    TYPE_BUSINESS = "business"    # Project Phoenix, Q4 goals
    TYPE_PERSON = "person"         # @alice, team members
    TYPE_TOOL = "tool"            # Slack, GitHub, Jenkins
    TYPE_CONCEPT = "concept"      # performance, optimization, migration

    # Common abbreviations (will be learned over time)
    ABBREVIATIONS = {
        "db": "database",
        "k8s": "kubernetes",
        "api": "API",
        "ui": "user interface",
        "ux": "user experience",
        "ml": "machine learning",
        "ai": "artificial intelligence",
        "ci": "continuous integration",
        "cd": "continuous deployment",
        "perf": "performance",
        "auth": "authentication",
        "config": "configuration",
        "repo": "repository",
        "prod": "production",
        "dev": "development",
        "qa": "quality assurance",
    }

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.use_llm = getattr(settings, 'enable_llm_entity_extraction', True)

    async def extract_and_store_entities(
        self,
        message_text: str,
        message_id: str,
        channel_id: str,
        user_id: str,
        team_id: str,
        db: AsyncSession
    ) -> List[Entity]:
        """Extract entities from message and store them"""
        try:
            # Extract entities
            if self.use_llm:
                entities = await self._extract_entities_llm(message_text)
            else:
                entities = self._extract_entities_regex(message_text)

            if not entities:
                return []

            stored_entities = []

            # Store each entity
            for entity_data in entities:
                entity = await self._get_or_create_entity(
                    entity_text=entity_data["text"],
                    entity_type=entity_data["type"],
                    channel_id=channel_id,
                    team_id=team_id,
                    db=db
                )
                stored_entities.append(entity)

            # Update co-occurrences (entities appearing together in same message)
            if len(stored_entities) > 1:
                await self._update_co_occurrences(stored_entities, channel_id, team_id, db)

            await db.commit()

            logger.info(
                "entities_extracted",
                message_id=message_id,
                entity_count=len(stored_entities),
                types=[e.entity_type for e in stored_entities],
                team_id=team_id
            )

            return stored_entities

        except Exception as e:
            logger.error("entity_extraction_error", error=str(e), message_id=message_id)
            await db.rollback()
            return []

    async def _extract_entities_llm(self, text: str) -> List[Dict[str, Any]]:
        """Extract entities using OpenAI GPT-4"""
        try:
            prompt = f"""Extract key entities from this Slack message. Identify:
- Technical terms (databases, services, APIs, technologies)
- Business concepts (project names, initiatives, products)
- Tools and platforms
- Important concepts being discussed

Message: "{text}"

Return JSON array of entities:
[
  {{"text": "entity text", "type": "technical|business|tool|concept"}},
  ...
]

Only extract significant entities (ignore common words). Limit to 10 most important."""

            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert at extracting key entities from technical discussions."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )

            content = response.choices[0].message.content.strip()

            # Parse JSON response
            # Remove markdown code blocks if present
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*$', '', content)

            entities = json.loads(content)

            # Validate and normalize
            validated = []
            for ent in entities:
                if isinstance(ent, dict) and "text" in ent and "type" in ent:
                    validated.append({
                        "text": ent["text"].strip(),
                        "type": ent["type"].lower()
                    })

            logger.info("llm_entity_extraction", entity_count=len(validated))
            return validated

        except Exception as e:
            logger.error("llm_entity_extraction_error", error=str(e))
            # Fallback to regex
            return self._extract_entities_regex(text)

    def _extract_entities_regex(self, text: str) -> List[Dict[str, Any]]:
        """Extract entities using regex patterns (fallback)"""
        entities = []

        # Technical patterns
        technical_patterns = [
            r'\b(postgres|postgresql|mysql|mongodb|redis|elasticsearch)\b',
            r'\b(kubernetes|k8s|docker|aws|azure|gcp)\b',
            r'\b(api|rest|graphql|grpc|http|https)\b',
            r'\b(python|javascript|java|go|rust|typescript)\b',
            r'\b(database|db|cache|queue|stream)\b',
        ]

        for pattern in technical_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                entities.append({
                    "text": match.group(0),
                    "type": self.TYPE_TECHNICAL
                })

        # Tool patterns
        tool_patterns = [
            r'\b(slack|github|gitlab|jira|jenkins|circleci)\b',
            r'\b(confluence|notion|figma|miro)\b',
        ]

        for pattern in tool_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                entities.append({
                    "text": match.group(0),
                    "type": self.TYPE_TOOL
                })

        # Concept patterns (performance, optimization, etc.)
        concept_patterns = [
            r'\b(performance|optimization|scalability|latency)\b',
            r'\b(migration|deployment|release|rollback)\b',
            r'\b(bug|issue|incident|outage)\b',
            r'\b(feature|enhancement|improvement)\b',
        ]

        for pattern in concept_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                entities.append({
                    "text": match.group(0),
                    "type": self.TYPE_CONCEPT
                })

        # Deduplicate
        seen = set()
        unique_entities = []
        for ent in entities:
            key = (ent["text"].lower(), ent["type"])
            if key not in seen:
                seen.add(key)
                unique_entities.append(ent)

        return unique_entities[:10]  # Limit to top 10

    async def _get_or_create_entity(
        self,
        entity_text: str,
        entity_type: str,
        channel_id: str,
        team_id: str,
        db: AsyncSession
    ) -> Entity:
        """Get existing entity or create new one"""

        # Normalize to canonical form
        canonical_form = self._normalize_entity(entity_text)

        # Check if entity exists (filter by team_id for multi-workspace isolation)
        result = await db.execute(
            select(Entity).where(
                and_(
                    Entity.canonical_form == canonical_form,
                    Entity.entity_type == entity_type,
                    Entity.team_id == team_id
                )
            )
        )
        entity = result.scalar_one_or_none()

        if entity:
            # Update statistics
            entity.occurrence_count += 1
            entity.last_seen_at = datetime.utcnow()

            # Update related channels
            if entity.related_channels is None:
                entity.related_channels = []
            if channel_id not in entity.related_channels:
                entity.related_channels.append(channel_id)
                entity.channel_count = len(entity.related_channels)

            logger.debug(
                "entity_updated",
                canonical_form=canonical_form,
                occurrence_count=entity.occurrence_count,
                team_id=team_id
            )
        else:
            # Create new entity
            entity = Entity(
                entity_text=entity_text,
                entity_type=entity_type,
                canonical_form=canonical_form,
                team_id=team_id,
                occurrence_count=1,
                channel_count=1,
                related_channels=[channel_id],
                related_entities={},
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow()
            )
            db.add(entity)

            logger.info(
                "entity_created",
                entity_text=entity_text,
                canonical_form=canonical_form,
                type=entity_type,
                team_id=team_id
            )

        return entity

    def _normalize_entity(self, text: str) -> str:
        """Normalize entity text to canonical form"""
        normalized = text.lower().strip()

        # Apply known abbreviations
        if normalized in self.ABBREVIATIONS:
            return self.ABBREVIATIONS[normalized]

        # Remove special characters
        normalized = re.sub(r'[^\w\s-]', '', normalized)

        # Normalize whitespace
        normalized = ' '.join(normalized.split())

        return normalized

    async def _update_co_occurrences(
        self,
        entities: List[Entity],
        channel_id: str,
        team_id: str,
        db: AsyncSession
    ):
        """Update entity relationships based on co-occurrence"""

        # Create pairs of entities that appeared together
        for i, entity1 in enumerate(entities):
            for entity2 in entities[i+1:]:
                # Skip if entities don't have IDs yet
                if entity1.id is None or entity2.id is None:
                    continue

                # Ensure entity1.id < entity2.id for consistency
                if entity1.id > entity2.id:
                    entity1, entity2 = entity2, entity1

                # Check if relationship exists (filter by team_id for multi-workspace isolation)
                result = await db.execute(
                    select(EntityRelationship).where(
                        and_(
                            EntityRelationship.entity_id_1 == entity1.id,
                            EntityRelationship.entity_id_2 == entity2.id,
                            EntityRelationship.relationship_type == "co_occurs_with",
                            EntityRelationship.team_id == team_id
                        )
                    ).limit(1)
                )
                relationship = result.scalar_one_or_none()

                if relationship:
                    # Update existing relationship
                    relationship.co_occurrence_count += 1
                    relationship.last_seen_at = datetime.utcnow()

                    # Update channels
                    if relationship.channels is None:
                        relationship.channels = []
                    if channel_id not in relationship.channels:
                        relationship.channels.append(channel_id)

                    # Recalculate confidence score
                    relationship.confidence_score = self._calculate_confidence(
                        relationship.co_occurrence_count,
                        entity1.occurrence_count,
                        entity2.occurrence_count
                    )
                else:
                    # Create new relationship
                    confidence = self._calculate_confidence(
                        1,  # First co-occurrence
                        entity1.occurrence_count,
                        entity2.occurrence_count
                    )

                    relationship = EntityRelationship(
                        entity_id_1=entity1.id,
                        entity_id_2=entity2.id,
                        relationship_type="co_occurs_with",
                        team_id=team_id,
                        co_occurrence_count=1,
                        confidence_score=confidence,
                        channels=[channel_id],
                        first_seen_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow()
                    )
                    db.add(relationship)

                    logger.debug(
                        "relationship_created",
                        entity1=entity1.canonical_form,
                        entity2=entity2.canonical_form,
                        team_id=team_id
                    )

    def _calculate_confidence(
        self,
        co_occurrence_count: int,
        entity1_count: int,
        entity2_count: int
    ) -> float:
        """Calculate confidence score for relationship using PMI (Pointwise Mutual Information)"""

        # Simple confidence based on co-occurrence frequency
        # More sophisticated: use PMI or other statistical measures

        # Normalize by min occurrence count
        min_count = min(entity1_count, entity2_count)
        if min_count == 0:
            return 0.0

        # Higher confidence if they co-occur frequently relative to individual occurrences
        confidence = min(1.0, co_occurrence_count / min_count)

        return round(confidence, 4)

    async def get_related_entities(
        self,
        entity_id: int,
        team_id: str,
        db: AsyncSession,
        limit: int = 10
    ) -> List[Tuple[Entity, float]]:
        """Get entities related to given entity, ordered by relationship strength"""

        try:
            # Find relationships where this entity is involved (filter by team_id)
            result = await db.execute(
                select(EntityRelationship).where(
                    and_(
                        or_(
                            EntityRelationship.entity_id_1 == entity_id,
                            EntityRelationship.entity_id_2 == entity_id
                        ),
                        EntityRelationship.team_id == team_id
                    )
                ).order_by(EntityRelationship.confidence_score.desc()).limit(limit)
            )
            relationships = result.scalars().all()

            # Get related entities
            related = []
            for rel in relationships:
                # Get the other entity in the relationship
                other_id = rel.entity_id_2 if rel.entity_id_1 == entity_id else rel.entity_id_1

                result = await db.execute(
                    select(Entity).where(
                        and_(
                            Entity.id == other_id,
                            Entity.team_id == team_id
                        )
                    )
                )
                other_entity = result.scalar_one_or_none()

                if other_entity:
                    related.append((other_entity, rel.confidence_score))

            return related

        except Exception as e:
            logger.error("get_related_entities_error", error=str(e), entity_id=entity_id, team_id=team_id)
            return []

    async def get_entity_by_text(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Entity]:
        """Get entity by text (checks canonical form)"""

        canonical = self._normalize_entity(entity_text)

        result = await db.execute(
            select(Entity).where(
                and_(
                    Entity.canonical_form == canonical,
                    Entity.team_id == team_id
                )
            )
        )
        return result.scalar_one_or_none()

    async def search_entities(
        self,
        query: str,
        team_id: str,
        entity_type: Optional[str] = None,
        db: AsyncSession = None,
        limit: int = 20
    ) -> List[Entity]:
        """Search for entities by text"""

        filters = [
            or_(
                Entity.entity_text.ilike(f"%{query}%"),
                Entity.canonical_form.ilike(f"%{query}%")
            ),
            Entity.team_id == team_id
        ]

        if entity_type:
            filters.append(Entity.entity_type == entity_type)

        result = await db.execute(
            select(Entity)
            .where(and_(*filters))
            .order_by(Entity.occurrence_count.desc())
            .limit(limit)
        )

        return result.scalars().all()


# Global instance
entity_service = EntityService()
