"""Knowledge Graph Service for advanced entity relationship queries"""
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc
from datetime import datetime, timedelta

from app.models.entity import Entity, EntityRelationship
from app.models.message import Message
from app.core.logging import get_logger

logger = get_logger(__name__)


class KnowledgeGraphService:
    """Advanced knowledge graph queries and analytics"""

    async def get_entity_context(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession,
        depth: int = 2
    ) -> Dict[str, Any]:
        """Get comprehensive context for an entity including relationships"""

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, team_id, db)

        if not entity:
            return {
                "found": False,
                "entity_text": entity_text
            }

        # Get related entities (1st degree)
        first_degree = await entity_service.get_related_entities(entity.id, team_id, db, limit=20)

        # Get 2nd degree relationships if depth > 1
        second_degree = []
        if depth > 1 and first_degree:
            for related_entity, confidence in first_degree[:5]:  # Top 5 only
                related_2nd = await entity_service.get_related_entities(
                    related_entity.id,
                    team_id,
                    db,
                    limit=5
                )
                second_degree.extend([
                    {
                        "via": related_entity.canonical_form,
                        "entity": e.canonical_form,
                        "confidence": conf
                    }
                    for e, conf in related_2nd
                ])

        # Get channel distribution
        channel_info = []
        if entity.related_channels:
            for channel_id in entity.related_channels:
                result = await db.execute(
                    select(func.count(Message.id))
                    .where(
                        and_(
                            Message.channel_id == channel_id,
                            Message.text.ilike(f"%{entity.canonical_form}%")
                        )
                    )
                )
                count = result.scalar()
                if count > 0:
                    channel_info.append({
                        "channel_id": channel_id,
                        "mention_count": count
                    })

        return {
            "found": True,
            "entity": {
                "text": entity.entity_text,
                "canonical_form": entity.canonical_form,
                "type": entity.entity_type,
                "occurrence_count": entity.occurrence_count,
                "channel_count": entity.channel_count,
                "first_seen": entity.first_seen_at.isoformat(),
                "last_seen": entity.last_seen_at.isoformat()
            },
            "related_entities": [
                {
                    "entity": e.canonical_form,
                    "type": e.entity_type,
                    "confidence": conf,
                    "occurrences": e.occurrence_count
                }
                for e, conf in first_degree
            ],
            "second_degree_relationships": second_degree[:10],
            "channel_distribution": sorted(channel_info, key=lambda x: x["mention_count"], reverse=True)[:10]
        }

    async def find_related_topics(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession,
        min_confidence: float = 0.3,
        limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Find all topics related to given entity"""

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, team_id, db)

        if not entity:
            return []

        related = await entity_service.get_related_entities(entity.id, team_id, db, limit=limit)

        topics = []
        for related_entity, confidence in related:
            if confidence >= min_confidence:
                topics.append({
                    "topic": related_entity.canonical_form,
                    "type": related_entity.entity_type,
                    "confidence": confidence,
                    "occurrences": related_entity.occurrence_count,
                    "channels": related_entity.channel_count
                })

        return topics

    async def get_cross_channel_discussions(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Analyze how an entity is discussed across different channels"""

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, team_id, db)

        if not entity or not entity.related_channels:
            return {"found": False}

        channel_discussions = []

        for channel_id in entity.related_channels:
            # Get messages in this channel
            result = await db.execute(
                select(Message)
                .where(
                    and_(
                        Message.channel_id == channel_id,
                        Message.text.ilike(f"%{entity.canonical_form}%")
                    )
                )
                .order_by(desc(Message.timestamp))
                .limit(5)
            )
            messages = result.scalars().all()

            if messages:
                # Categorize discussion type (simple keyword analysis)
                technical_keywords = ["api", "database", "code", "error", "bug", "performance"]
                problem_keywords = ["issue", "problem", "down", "failing", "broken"]
                solution_keywords = ["fix", "solved", "resolved", "solution", "workaround"]

                technical_count = sum(
                    1 for msg in messages
                    if any(kw in (msg.text or "").lower() for kw in technical_keywords)
                )
                problem_count = sum(
                    1 for msg in messages
                    if any(kw in (msg.text or "").lower() for kw in problem_keywords)
                )
                solution_count = sum(
                    1 for msg in messages
                    if any(kw in (msg.text or "").lower() for kw in solution_keywords)
                )

                # Determine primary discussion type
                if technical_count > problem_count and technical_count > solution_count:
                    discussion_type = "technical"
                elif problem_count > solution_count:
                    discussion_type = "problems"
                else:
                    discussion_type = "solutions"

                channel_discussions.append({
                    "channel_id": channel_id,
                    "message_count": len(messages),
                    "discussion_type": discussion_type,
                    "latest_mention": messages[0].timestamp.isoformat(),
                    "sample_messages": [
                        {
                            "text": msg.text[:200],
                            "user": msg.user_name,
                            "timestamp": msg.timestamp.isoformat()
                        }
                        for msg in messages[:3]
                    ]
                })

        return {
            "found": True,
            "entity": entity.canonical_form,
            "total_channels": len(channel_discussions),
            "channel_discussions": sorted(
                channel_discussions,
                key=lambda x: x["message_count"],
                reverse=True
            )
        }

    async def find_entity_timeline(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession,
        days_back: int = 90
    ) -> Dict[str, Any]:
        """Get temporal analysis of entity mentions"""

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, team_id, db)

        if not entity:
            return {"found": False}

        # Get messages mentioning this entity
        since_date = datetime.utcnow() - timedelta(days=days_back)

        result = await db.execute(
            select(Message)
            .where(
                and_(
                    Message.text.ilike(f"%{entity.canonical_form}%"),
                    Message.timestamp >= since_date
                )
            )
            .order_by(Message.timestamp.asc())
        )
        messages = result.scalars().all()

        if not messages:
            return {
                "found": True,
                "entity": entity.canonical_form,
                "messages_found": 0
            }

        # Group by week
        weekly_counts = {}
        for msg in messages:
            week_key = msg.timestamp.strftime("%Y-W%W")
            if week_key not in weekly_counts:
                weekly_counts[week_key] = 0
            weekly_counts[week_key] += 1

        # Find spike periods (weeks with >2x average mentions)
        avg_weekly = sum(weekly_counts.values()) / max(len(weekly_counts), 1)
        spikes = [
            {"week": week, "count": count}
            for week, count in weekly_counts.items()
            if count > avg_weekly * 2
        ]

        return {
            "found": True,
            "entity": entity.canonical_form,
            "total_mentions": len(messages),
            "date_range": {
                "start": messages[0].timestamp.isoformat(),
                "end": messages[-1].timestamp.isoformat()
            },
            "weekly_mentions": weekly_counts,
            "average_weekly_mentions": round(avg_weekly, 2),
            "spike_periods": sorted(spikes, key=lambda x: x["count"], reverse=True),
            "first_mention": {
                "text": messages[0].text[:200],
                "user": messages[0].user_name,
                "timestamp": messages[0].timestamp.isoformat(),
                "channel": messages[0].channel_name
            },
            "latest_mention": {
                "text": messages[-1].text[:200],
                "user": messages[-1].user_name,
                "timestamp": messages[-1].timestamp.isoformat(),
                "channel": messages[-1].channel_name
            }
        }

    async def get_top_entities(
        self,
        team_id: str,
        db: AsyncSession,
        entity_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get most frequently mentioned entities"""

        filters = [Entity.team_id == team_id]
        if entity_type:
            filters.append(Entity.entity_type == entity_type)

        query = select(Entity).order_by(desc(Entity.occurrence_count)).limit(limit)

        if filters:
            query = query.where(and_(*filters))

        result = await db.execute(query)
        entities = result.scalars().all()

        return [
            {
                "entity": e.canonical_form,
                "type": e.entity_type,
                "occurrences": e.occurrence_count,
                "channels": e.channel_count,
                "first_seen": e.first_seen_at.isoformat(),
                "last_seen": e.last_seen_at.isoformat()
            }
            for e in entities
        ]

    async def get_entity_cluster(
        self,
        entity_text: str,
        team_id: str,
        db: AsyncSession,
        min_confidence: float = 0.4
    ) -> Dict[str, Any]:
        """Get tightly connected entity cluster (entities that frequently co-occur)"""

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, team_id, db)

        if not entity:
            return {"found": False}

        # Get strongly related entities
        related = await entity_service.get_related_entities(entity.id, team_id, db, limit=30)

        # Filter by confidence threshold
        cluster_entities = [
            {
                "entity": e.canonical_form,
                "type": e.entity_type,
                "confidence": conf,
                "occurrences": e.occurrence_count
            }
            for e, conf in related
            if conf >= min_confidence
        ]

        # Get relationships between cluster members
        cluster_relationships = []
        for i, (e1, conf1) in enumerate(related[:10]):
            for e2, conf2 in related[i+1:10]:
                # Check if e1 and e2 are also related
                result = await db.execute(
                    select(EntityRelationship)
                    .where(
                        or_(
                            and_(
                                EntityRelationship.entity_id_1 == min(e1.id, e2.id),
                                EntityRelationship.entity_id_2 == max(e1.id, e2.id)
                            )
                        )
                    )
                )
                rel = result.scalar_one_or_none()

                if rel and rel.confidence_score >= min_confidence:
                    cluster_relationships.append({
                        "entity1": e1.canonical_form,
                        "entity2": e2.canonical_form,
                        "confidence": rel.confidence_score
                    })

        return {
            "found": True,
            "center_entity": entity.canonical_form,
            "cluster_size": len(cluster_entities),
            "cluster_entities": cluster_entities,
            "internal_relationships": cluster_relationships
        }


# Global instance
knowledge_graph_service = KnowledgeGraphService()
