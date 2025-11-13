"""
Daily Digest Service

Generates intelligent daily digests for teams showing:
- Hot topics and trending entities
- Active discussions with high engagement
- New cross-source connections discovered
- Expert activity and helpful responses
- Unresolved questions and knowledge gaps

This creates proactive value and demonstrates the knowledge graph intelligence.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc

from app.core.logging import get_logger
from app.models.message import Message
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType
from app.services.slack_client import slack_client_manager

logger = get_logger(__name__)


class DailyDigestService:
    """
    Generates daily intelligence digests for teams

    Analyzes the past 24 hours (configurable) to surface:
    - Trending topics and technologies
    - High-engagement discussions
    - Cross-source discoveries
    - Expert contributions
    - Knowledge gaps
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_digest(
        self,
        team_id: str,
        hours_back: int = 24,
        min_mentions: int = 3
    ) -> Dict[str, Any]:
        """
        Generate comprehensive daily digest

        Args:
            team_id: Workspace team ID
            hours_back: How many hours to look back
            min_mentions: Minimum mentions to be considered "hot"

        Returns:
            Dict with all digest sections
        """
        try:
            logger.info(
                "generating_digest",
                team_id=team_id,
                hours_back=hours_back
            )

            cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

            # Run analyses sequentially (async session doesn't support concurrency)
            hot_topics = await self._get_hot_topics(team_id, cutoff_time, min_mentions)
            active_discussions = await self._get_active_discussions(team_id, cutoff_time)
            cross_source_links = await self._get_new_cross_source_links(team_id, cutoff_time)
            expert_activity = await self._get_expert_activity(team_id, cutoff_time)
            unresolved_questions = await self._get_unresolved_questions(team_id, cutoff_time)

            digest = {
                'team_id': team_id,
                'generated_at': datetime.utcnow().isoformat(),
                'period_hours': hours_back,
                'hot_topics': hot_topics,
                'active_discussions': active_discussions,
                'cross_source_links': cross_source_links,
                'expert_activity': expert_activity,
                'unresolved_questions': unresolved_questions,
                'summary': self._generate_summary(
                    hot_topics,
                    active_discussions,
                    cross_source_links,
                    expert_activity,
                    unresolved_questions
                )
            }

            logger.info(
                "digest_generated",
                team_id=team_id,
                hot_topics_count=len(hot_topics),
                discussions_count=len(active_discussions),
                links_count=len(cross_source_links)
            )

            return digest

        except Exception as e:
            logger.error("generate_digest_error", error=str(e), team_id=team_id)
            raise

    async def _get_hot_topics(
        self,
        team_id: str,
        cutoff_time: datetime,
        min_mentions: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Find trending topics and technologies

        Looks at entity nodes (topics, technologies) and counts how many
        times they were mentioned in recent messages.
        """
        try:
            # Get entity nodes
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == 'derived',
                        or_(
                            CrossSourceNode.node_type == NodeType.TOPIC,
                            CrossSourceNode.node_type == NodeType.TECHNOLOGY
                        )
                    )
                )
            )
            entities = result.scalars().all()

            # Count mentions in recent messages
            topics_with_counts = []

            for entity in entities:
                entity_name = entity.title.lower()

                # Count messages mentioning this entity
                count_result = await self.db.execute(
                    select(func.count(Message.id)).where(
                        and_(
                            Message.team_id == team_id,
                            Message.created_at >= cutoff_time,
                            or_(
                                Message.text.ilike(f'%{entity_name}%'),
                                Message.text_processed.ilike(f'%{entity_name}%')
                            )
                        )
                    )
                )
                mention_count = count_result.scalar()

                if mention_count >= min_mentions:
                    # Get connected GitHub items
                    github_links = await self._get_github_connections(entity.canonical_id)

                    topics_with_counts.append({
                        'entity': entity.title,
                        'entity_type': entity.node_type.value,
                        'mention_count': mention_count,
                        'canonical_id': entity.canonical_id,
                        'github_connections': len(github_links),
                        'github_links': github_links[:3]  # Top 3
                    })

            # Sort by mention count
            topics_with_counts.sort(key=lambda x: x['mention_count'], reverse=True)

            return topics_with_counts[:10]  # Top 10

        except Exception as e:
            logger.error("get_hot_topics_error", error=str(e))
            return []

    async def _get_active_discussions(
        self,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Find discussions with high engagement

        Looks for threads with many replies or reactions.
        """
        try:
            # Get messages that are thread parents
            result = await self.db.execute(
                select(Message).where(
                    and_(
                        Message.team_id == team_id,
                        Message.created_at >= cutoff_time,
                        Message.is_thread_parent == True  # Only thread parent messages
                    )
                ).order_by(desc(Message.created_at)).limit(100)
            )
            messages = result.scalars().all()

            discussions = []

            for msg in messages:
                # Count replies (messages with this message as thread_ts)
                reply_result = await self.db.execute(
                    select(func.count(Message.id)).where(
                        and_(
                            Message.team_id == team_id,
                            Message.thread_ts == msg.message_id
                        )
                    )
                )
                reply_count = reply_result.scalar()

                # Count reactions (if stored in metadata)
                reaction_count = 0
                if msg.metadata and isinstance(msg.metadata, dict):
                    reactions = msg.metadata.get('reactions', [])
                    reaction_count = sum(r.get('count', 0) for r in reactions)

                engagement_score = reply_count + (reaction_count * 0.5)

                if engagement_score >= 3:  # At least 3 replies or 6 reactions
                    discussions.append({
                        'message_id': msg.message_id,
                        'channel_id': msg.channel_id,
                        'text': msg.text[:200] if msg.text else '',  # Preview
                        'author': msg.user_id,
                        'reply_count': reply_count,
                        'reaction_count': reaction_count,
                        'engagement_score': engagement_score,
                        'created_at': msg.created_at.isoformat()
                    })

            # Sort by engagement
            discussions.sort(key=lambda x: x['engagement_score'], reverse=True)

            return discussions[:5]  # Top 5

        except Exception as e:
            logger.error("get_active_discussions_error", error=str(e))
            return []

    async def _get_new_cross_source_links(
        self,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Find newly discovered Slack ↔ GitHub connections

        Shows edges created in the last period connecting different sources.
        """
        try:
            result = await self.db.execute(
                select(CrossSourceEdge).where(
                    and_(
                        CrossSourceEdge.team_id == team_id,
                        CrossSourceEdge.created_at >= cutoff_time
                    )
                ).limit(20)
            )
            edges = result.scalars().all()

            cross_source_connections = []

            for edge in edges:
                # Load source and target nodes
                source_node = await self._get_node(edge.source_node_id)
                target_node = await self._get_node(edge.target_node_id)

                if not source_node or not target_node:
                    continue

                # Only include if different sources
                if source_node.source != target_node.source:
                    cross_source_connections.append({
                        'source': {
                            'title': source_node.title,
                            'type': source_node.node_type.value,
                            'source': source_node.source,
                            'url': source_node.url
                        },
                        'target': {
                            'title': target_node.title,
                            'type': target_node.node_type.value,
                            'source': target_node.source,
                            'url': target_node.url
                        },
                        'relationship': edge.edge_type.value,
                        'confidence': edge.confidence,
                        'discovered_at': edge.created_at.isoformat()
                    })

            return cross_source_connections[:5]  # Top 5

        except Exception as e:
            logger.error("get_cross_source_links_error", error=str(e))
            return []

    async def _get_expert_activity(
        self,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Identify who's been most helpful

        Looks at message counts, reaction counts, and thread participation.
        """
        try:
            # Get user activity
            result = await self.db.execute(
                select(
                    Message.user_id,
                    func.count(Message.id).label('message_count')
                ).where(
                    and_(
                        Message.team_id == team_id,
                        Message.created_at >= cutoff_time,
                        Message.user_id.isnot(None)
                    )
                ).group_by(Message.user_id).order_by(desc('message_count')).limit(10)
            )
            user_stats = result.all()

            experts = []
            for user_id, message_count in user_stats:
                if message_count >= 5:  # At least 5 messages
                    experts.append({
                        'user_id': user_id,
                        'message_count': message_count,
                        'activity_score': message_count  # Could add reaction weights
                    })

            return experts[:5]  # Top 5

        except Exception as e:
            logger.error("get_expert_activity_error", error=str(e))
            return []

    async def _get_unresolved_questions(
        self,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Find questions that haven't been answered

        Looks for messages with question marks that have few/no replies.
        """
        try:
            # Get messages with question marks
            result = await self.db.execute(
                select(Message).where(
                    and_(
                        Message.team_id == team_id,
                        Message.created_at >= cutoff_time,
                        Message.text.like('%?%')
                    )
                ).order_by(desc(Message.created_at)).limit(50)
            )
            question_messages = result.scalars().all()

            unresolved = []

            for msg in question_messages:
                # Skip if it's a reply in a thread
                if msg.thread_ts and msg.thread_ts != msg.message_id:
                    continue

                # Count replies
                reply_result = await self.db.execute(
                    select(func.count(Message.id)).where(
                        and_(
                            Message.team_id == team_id,
                            Message.thread_ts == msg.message_id,
                            Message.message_id != msg.message_id  # Don't count self
                        )
                    )
                )
                reply_count = reply_result.scalar()

                # Consider unresolved if 0-1 replies
                if reply_count <= 1:
                    unresolved.append({
                        'message_id': msg.message_id,
                        'channel_id': msg.channel_id,
                        'text': msg.text[:200] if msg.text else '',
                        'author': msg.user_id,
                        'reply_count': reply_count,
                        'created_at': msg.created_at.isoformat()
                    })

            return unresolved[:5]  # Top 5

        except Exception as e:
            logger.error("get_unresolved_questions_error", error=str(e))
            return []

    async def _get_github_connections(self, entity_id: str) -> List[Dict[str, Any]]:
        """Get GitHub items connected to an entity"""
        try:
            result = await self.db.execute(
                select(CrossSourceEdge).where(
                    or_(
                        CrossSourceEdge.source_node_id == entity_id,
                        CrossSourceEdge.target_node_id == entity_id
                    )
                ).limit(10)
            )
            edges = result.scalars().all()

            github_items = []
            for edge in edges:
                other_id = (
                    edge.target_node_id if edge.source_node_id == entity_id
                    else edge.source_node_id
                )

                node = await self._get_node(other_id)
                if node and node.source == 'github':
                    github_items.append({
                        'title': node.title,
                        'type': node.node_type.value,
                        'url': node.url
                    })

            return github_items

        except Exception as e:
            logger.error("get_github_connections_error", error=str(e))
            return []

    async def _get_node(self, canonical_id: str) -> Optional[CrossSourceNode]:
        """Get node by canonical ID"""
        try:
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    CrossSourceNode.canonical_id == canonical_id
                )
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error("get_node_error", error=str(e))
            return None

    def _generate_summary(
        self,
        hot_topics: List,
        active_discussions: List,
        cross_source_links: List,
        expert_activity: List,
        unresolved_questions: List
    ) -> str:
        """Generate human-readable summary"""
        parts = []

        if hot_topics:
            top_topic = hot_topics[0]
            parts.append(
                f"🔥 Hottest topic: **{top_topic['entity']}** "
                f"({top_topic['mention_count']} mentions)"
            )

        if active_discussions:
            parts.append(f"💬 {len(active_discussions)} active discussions")

        if cross_source_links:
            parts.append(f"🔗 {len(cross_source_links)} new Slack ↔ GitHub connections discovered")

        if expert_activity:
            parts.append(f"⭐ {len(expert_activity)} active contributors")

        if unresolved_questions:
            parts.append(f"❓ {len(unresolved_questions)} questions need answers")

        return " | ".join(parts) if parts else "Quiet day in the workspace"

    async def format_for_slack(self, digest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Format digest as Slack blocks

        Returns a beautiful Slack message with sections for each insight.
        """
        blocks = []

        # Header
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Daily Intelligence Digest",
                "emoji": True
            }
        })

        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Last {digest['period_hours']} hours • {digest['summary']}"
            }]
        })

        blocks.append({"type": "divider"})

        # Hot Topics
        if digest['hot_topics']:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🔥 Trending Topics*"
                }
            })

            for topic in digest['hot_topics'][:5]:
                github_info = ""
                if topic['github_connections'] > 0:
                    github_info = f" • Linked to {topic['github_connections']} GitHub items"

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• *{topic['entity']}* - {topic['mention_count']} mentions{github_info}"
                    }
                })

            blocks.append({"type": "divider"})

        # Active Discussions
        if digest['active_discussions']:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*💬 Most Active Discussions*"
                }
            })

            for disc in digest['active_discussions'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• {disc['text'][:100]}...\n"
                               f"  {disc['reply_count']} replies, {disc['reaction_count']} reactions"
                    }
                })

            blocks.append({"type": "divider"})

        # Cross-Source Links
        if digest['cross_source_links']:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🔗 New Slack ↔ GitHub Connections*"
                }
            })

            for link in digest['cross_source_links'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• <{link['source']['url']}|{link['source']['title']}> "
                               f"→ <{link['target']['url']}|{link['target']['title']}>"
                    }
                })

            blocks.append({"type": "divider"})

        # Unresolved Questions
        if digest['unresolved_questions']:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*❓ Questions Needing Answers*"
                }
            })

            for q in digest['unresolved_questions'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• {q['text'][:150]}..."
                    }
                })

        # Footer
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "💡 This digest is powered by your team's knowledge graph • Feedback helps me improve"
            }]
        })

        return blocks

    async def send_digest_to_channel(
        self,
        team_id: str,
        channel_id: str,
        digest: Dict[str, Any]
    ) -> bool:
        """
        Send formatted digest to a Slack channel

        Args:
            team_id: Workspace team ID
            channel_id: Channel to post to
            digest: Generated digest data

        Returns:
            True if successful
        """
        try:
            blocks = await self.format_for_slack(digest)

            await slack_client_manager.post_message(
                team_id=team_id,
                channel_id=channel_id,
                text="Daily Intelligence Digest",
                blocks=blocks
            )

            logger.info(
                "digest_sent",
                team_id=team_id,
                channel_id=channel_id
            )

            return True

        except Exception as e:
            logger.error(
                "send_digest_error",
                error=str(e),
                team_id=team_id,
                channel_id=channel_id
            )
            return False


def get_daily_digest_service(db: AsyncSession) -> DailyDigestService:
    """Get daily digest service instance"""
    return DailyDigestService(db)
