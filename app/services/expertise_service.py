"""User expertise tracking and calculation service"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_, func, desc
from collections import defaultdict

from app.models.user_expertise import UserExpertise
from app.models.entity import Entity
from app.models.message import Message
from app.core.logging import get_logger

logger = get_logger(__name__)


class ExpertiseService:
    """Calculate and track user expertise in various topics"""

    def __init__(self):
        # Decay factor for recent activity (older messages count less)
        self.decay_days = 90
        self.decay_factor = 0.5

    async def calculate_user_expertise(
        self,
        user_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Calculate expertise scores for a user across all entities they've interacted with"""

        try:
            # Get all messages from this user
            result = await db.execute(
                select(Message)
                .where(Message.user_id == user_id)
                .order_by(Message.timestamp.desc())
            )
            messages = result.scalars().all()

            if not messages:
                logger.info("no_messages_for_user", user_id=user_id)
                return []

            # Extract entities from user's messages
            from app.services.entity_service import entity_service

            entity_interactions = defaultdict(lambda: {
                'messages': [],
                'mention_count': 0,
                'reactions': 0,
                'replies': 0,
                'channels': set()
            })

            for message in messages:
                if not message.text_processed or len(message.text_processed) < 10:
                    continue

                # Extract entities from this message
                entities = await entity_service.extract_and_store_entities(
                    message_text=message.text_processed,
                    message_id=message.message_id,
                    channel_id=message.channel_id,
                    user_id=user_id,
                    db=db
                )

                for entity in entities:
                    entity_interactions[entity.id]['messages'].append(message)
                    entity_interactions[entity.id]['mention_count'] += 1
                    entity_interactions[entity.id]['reactions'] += message.reaction_count or 0
                    entity_interactions[entity.id]['replies'] += message.reply_count or 0
                    entity_interactions[entity.id]['channels'].add(message.channel_id)

            # Calculate expertise scores
            expertise_records = []

            for entity_id, data in entity_interactions.items():
                if data['mention_count'] < 2:  # Skip if only mentioned once
                    continue

                # Get entity
                result = await db.execute(
                    select(Entity).where(Entity.id == entity_id)
                )
                entity = result.scalar_one_or_none()

                if not entity:
                    continue

                # Calculate scores
                scores = self._calculate_expertise_scores(
                    message_count=len(data['messages']),
                    mention_count=data['mention_count'],
                    reactions=data['reactions'],
                    replies=data['replies'],
                    messages=data['messages']
                )

                # Get or create UserExpertise record
                expertise = await self._get_or_create_expertise(
                    user_id=user_id,
                    entity_id=entity_id,
                    db=db
                )

                # Update expertise
                expertise.mention_count = data['mention_count']
                expertise.message_count = len(data['messages'])
                expertise.reactions_received = data['reactions']
                expertise.replies_received = data['replies']
                expertise.engagement_score = scores['engagement']
                expertise.authority_score = scores['authority']
                expertise.expertise_score = scores['overall']
                expertise.recent_activity_score = scores['recency']
                expertise.primary_channels = list(data['channels'])[:5]
                expertise.last_interaction = data['messages'][0].timestamp
                expertise.first_interaction = data['messages'][-1].timestamp

                # Store sample high-quality messages
                high_quality = sorted(
                    data['messages'],
                    key=lambda m: (m.reaction_count or 0) + (m.reply_count or 0) * 2,
                    reverse=True
                )[:3]

                expertise.sample_messages = [
                    {
                        'text': msg.text[:300],
                        'timestamp': msg.timestamp.isoformat(),
                        'channel': msg.channel_name,
                        'reactions': msg.reaction_count,
                        'replies': msg.reply_count
                    }
                    for msg in high_quality
                ]

                expertise_records.append({
                    'entity': entity.canonical_form,
                    'entity_type': entity.entity_type,
                    'expertise_score': scores['overall'],
                    'mention_count': data['mention_count'],
                    'engagement_score': scores['engagement']
                })

            await db.commit()

            logger.info(
                "expertise_calculated",
                user_id=user_id,
                entity_count=len(expertise_records)
            )

            return sorted(expertise_records, key=lambda x: x['expertise_score'], reverse=True)

        except Exception as e:
            logger.error("expertise_calculation_error", error=str(e), user_id=user_id)
            await db.rollback()
            return []

    def _calculate_expertise_scores(
        self,
        message_count: int,
        mention_count: int,
        reactions: int,
        replies: int,
        messages: List[Message]
    ) -> Dict[str, float]:
        """Calculate various expertise scores"""

        # Engagement score (0-1)
        # Based on reactions and replies received
        avg_reactions = reactions / max(message_count, 1)
        avg_replies = replies / max(message_count, 1)
        engagement = min(1.0, (avg_reactions * 0.3 + avg_replies * 0.7) / 10)

        # Authority score (0-1)
        # Based on consistency and volume
        authority = min(1.0, (
            (message_count / 50) * 0.4 +  # Volume factor
            (mention_count / 100) * 0.3 +  # Frequency factor
            engagement * 0.3                # Quality factor
        ))

        # Recency score (0-1)
        # Decay older messages
        now = datetime.utcnow()
        decay_threshold = now - timedelta(days=self.decay_days)

        recent_count = sum(1 for msg in messages if msg.timestamp > decay_threshold)
        recency = min(1.0, recent_count / max(message_count, 1))

        # Overall expertise score (0-1)
        overall = (
            authority * 0.4 +
            engagement * 0.3 +
            recency * 0.3
        )

        return {
            'engagement': round(engagement, 4),
            'authority': round(authority, 4),
            'recency': round(recency, 4),
            'overall': round(overall, 4)
        }

    async def _get_or_create_expertise(
        self,
        user_id: str,
        entity_id: int,
        db: AsyncSession
    ) -> UserExpertise:
        """Get existing expertise record or create new one"""

        result = await db.execute(
            select(UserExpertise).where(
                and_(
                    UserExpertise.user_id == user_id,
                    UserExpertise.entity_id == entity_id
                )
            )
        )
        expertise = result.scalar_one_or_none()

        if not expertise:
            expertise = UserExpertise(
                user_id=user_id,
                entity_id=entity_id,
                first_interaction=datetime.utcnow(),
                last_interaction=datetime.utcnow()
            )
            db.add(expertise)

        return expertise

    async def get_user_top_expertise(
        self,
        user_id: str,
        db: AsyncSession,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get user's top expertise areas"""

        result = await db.execute(
            select(UserExpertise, Entity)
            .join(Entity, UserExpertise.entity_id == Entity.id)
            .where(UserExpertise.user_id == user_id)
            .order_by(desc(UserExpertise.expertise_score))
            .limit(limit)
        )

        rows = result.all()

        return [
            {
                'entity': entity.canonical_form,
                'entity_type': entity.entity_type,
                'expertise_score': expertise.expertise_score,
                'mention_count': expertise.mention_count,
                'engagement_score': expertise.engagement_score,
                'authority_score': expertise.authority_score,
                'last_interaction': expertise.last_interaction.isoformat(),
                'primary_channels': expertise.primary_channels
            }
            for expertise, entity in rows
        ]

    async def find_experts(
        self,
        entity_text: str,
        db: AsyncSession,
        min_score: float = 0.3,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Find experts for a given entity/topic"""

        from app.services.entity_service import entity_service

        # Get entity
        entity = await entity_service.get_entity_by_text(entity_text, db)

        if not entity:
            return []

        # Get experts
        result = await db.execute(
            select(UserExpertise)
            .where(
                and_(
                    UserExpertise.entity_id == entity.id,
                    UserExpertise.expertise_score >= min_score
                )
            )
            .order_by(desc(UserExpertise.expertise_score))
            .limit(limit)
        )

        expertise_records = result.scalars().all()

        # Get user names
        from app.models.user import User

        experts = []
        for exp in expertise_records:
            result = await db.execute(
                select(User).where(User.user_id == exp.user_id)
            )
            user = result.scalar_one_or_none()

            experts.append({
                'user_id': exp.user_id,
                'user_name': user.display_name if user else exp.user_id,
                'expertise_score': exp.expertise_score,
                'mention_count': exp.mention_count,
                'engagement_score': exp.engagement_score,
                'authority_score': exp.authority_score,
                'sample_messages': exp.sample_messages,
                'primary_channels': exp.primary_channels
            })

        return experts

    async def get_user_profile(
        self,
        user_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Get comprehensive user expertise profile"""

        # Get top expertise areas
        top_expertise = await self.get_user_top_expertise(user_id, db, limit=20)

        if not top_expertise:
            return {
                'user_id': user_id,
                'expertise_areas': [],
                'total_areas': 0
            }

        # Calculate overall stats
        total_mentions = sum(e['mention_count'] for e in top_expertise)
        avg_engagement = sum(e['engagement_score'] for e in top_expertise) / len(top_expertise)

        # Group by entity type
        by_type = defaultdict(list)
        for exp in top_expertise:
            by_type[exp['entity_type']].append(exp)

        return {
            'user_id': user_id,
            'top_expertise': top_expertise[:10],
            'total_areas': len(top_expertise),
            'total_mentions': total_mentions,
            'avg_engagement': round(avg_engagement, 4),
            'expertise_by_type': {
                type_name: {
                    'count': len(items),
                    'top_areas': [e['entity'] for e in items[:5]]
                }
                for type_name, items in by_type.items()
            }
        }


# Global instance
expertise_service = ExpertiseService()
