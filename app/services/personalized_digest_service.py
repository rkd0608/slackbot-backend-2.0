"""
Personalized Daily Digest Service

Generates and sends personalized daily digests to users via DM.

Key features:
- User-specific content based on their activity and preferences
- Respects user preferences (mute, frequency, content filters)
- Sends via DM instead of channel broadcasts
- Tracks delivery and engagement
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc

from app.core.logging import get_logger
from app.models.message import Message
from app.models.user_preferences import UserPreferences
from app.services.daily_digest_service import DailyDigestService
from app.services.slack_client import slack_client_manager

logger = get_logger(__name__)


class PersonalizedDigestService:
    """
    Generates personalized digests for individual users

    Unlike team-wide digests, these are:
    - Filtered to user's channels and interests
    - Include user-specific mentions
    - Highlight threads user participated in
    - Respectful of user preferences
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.base_digest_service = DailyDigestService(db)

    async def generate_personalized_digest(
        self,
        user_id: str,
        team_id: str,
        hours_back: int = 24
    ) -> Optional[Dict[str, Any]]:
        """
        Generate personalized digest for a specific user

        Args:
            user_id: Slack user ID
            team_id: Workspace team ID
            hours_back: Hours to look back

        Returns:
            Personalized digest dict or None if user has disabled
        """
        try:
            logger.info(
                "generating_personalized_digest",
                user_id=user_id,
                team_id=team_id
            )

            # Get user preferences
            prefs = await self._get_or_create_preferences(user_id, team_id)

            # Check if digest is enabled
            if not prefs.daily_digest_enabled:
                logger.info(
                    "digest_disabled_for_user",
                    user_id=user_id,
                    team_id=team_id
                )
                return None

            # Check if it's time to send (frequency)
            if not await self._should_send_digest(prefs):
                logger.info(
                    "digest_not_due",
                    user_id=user_id,
                    last_sent=prefs.last_digest_sent
                )
                return None

            cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

            # Generate base digest
            base_digest = await self.base_digest_service.generate_digest(
                team_id=team_id,
                hours_back=hours_back,
                min_mentions=prefs.min_topic_mentions
            )

            # Personalize content
            personalized = await self._personalize_content(
                base_digest=base_digest,
                user_id=user_id,
                team_id=team_id,
                cutoff_time=cutoff_time,
                prefs=prefs
            )

            # Add user-specific sections
            personalized['mentions'] = await self._get_user_mentions(
                user_id, team_id, cutoff_time
            )

            personalized['my_threads'] = await self._get_user_active_threads(
                user_id, team_id, cutoff_time
            )

            # Update summary
            personalized['summary'] = self._generate_personalized_summary(personalized)

            logger.info(
                "personalized_digest_generated",
                user_id=user_id,
                team_id=team_id,
                sections=len([k for k, v in personalized.items() if v])
            )

            return personalized

        except Exception as e:
            logger.error(
                "generate_personalized_digest_error",
                error=str(e),
                user_id=user_id,
                team_id=team_id
            )
            return None

    async def _get_or_create_preferences(
        self,
        user_id: str,
        team_id: str
    ) -> UserPreferences:
        """Get user preferences or create with defaults"""
        result = await self.db.execute(
            select(UserPreferences).where(
                and_(
                    UserPreferences.user_id == user_id,
                    UserPreferences.team_id == team_id
                )
            )
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            # Create default preferences
            prefs = UserPreferences(
                user_id=user_id,
                team_id=team_id,
                daily_digest_enabled=True,
                digest_frequency="daily"
            )
            self.db.add(prefs)
            await self.db.commit()
            await self.db.refresh(prefs)

            logger.info(
                "user_preferences_created",
                user_id=user_id,
                team_id=team_id
            )

        return prefs

    async def _should_send_digest(self, prefs: UserPreferences) -> bool:
        """Check if digest should be sent based on frequency and last send time"""
        if not prefs.last_digest_sent:
            return True  # Never sent before

        now = datetime.utcnow()
        time_since_last = now - prefs.last_digest_sent

        if prefs.digest_frequency == "daily":
            return time_since_last >= timedelta(hours=20)  # Allow some slack
        elif prefs.digest_frequency == "weekly":
            return time_since_last >= timedelta(days=6, hours=20)
        else:  # "never"
            return False

    async def _personalize_content(
        self,
        base_digest: Dict[str, Any],
        user_id: str,
        team_id: str,
        cutoff_time: datetime,
        prefs: UserPreferences
    ) -> Dict[str, Any]:
        """Filter and personalize digest content based on preferences"""
        personalized = {
            'team_id': team_id,
            'user_id': user_id,
            'generated_at': datetime.utcnow().isoformat(),
            'period_hours': base_digest['period_hours']
        }

        # Filter sections based on preferences
        if prefs.include_hot_topics:
            personalized['hot_topics'] = base_digest['hot_topics']
        else:
            personalized['hot_topics'] = []

        if prefs.include_discussions:
            personalized['active_discussions'] = base_digest['active_discussions']
        else:
            personalized['active_discussions'] = []

        if prefs.include_github_links:
            personalized['cross_source_links'] = base_digest['cross_source_links']
        else:
            personalized['cross_source_links'] = []

        if prefs.include_questions:
            personalized['unresolved_questions'] = base_digest['unresolved_questions']
        else:
            personalized['unresolved_questions'] = []

        # Expert activity is always included (but not highlighted if it's the user)
        personalized['expert_activity'] = [
            e for e in base_digest['expert_activity']
            if e['user_id'] != user_id  # Don't show user their own stats
        ]

        return personalized

    async def _get_user_mentions(
        self,
        user_id: str,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """Get messages where user was mentioned"""
        try:
            result = await self.db.execute(
                select(Message).where(
                    and_(
                        Message.team_id == team_id,
                        Message.created_at >= cutoff_time,
                        Message.mentioned_users.like(f'%{user_id}%')
                    )
                ).order_by(desc(Message.created_at)).limit(10)
            )
            messages = result.scalars().all()

            mentions = []
            for msg in messages:
                mentions.append({
                    'message_id': msg.message_id,
                    'channel_id': msg.channel_id,
                    'text': msg.text[:200] if msg.text else '',
                    'author': msg.user_id,
                    'created_at': msg.created_at.isoformat()
                })

            return mentions[:5]  # Top 5

        except Exception as e:
            logger.error("get_user_mentions_error", error=str(e))
            return []

    async def _get_user_active_threads(
        self,
        user_id: str,
        team_id: str,
        cutoff_time: datetime
    ) -> List[Dict[str, Any]]:
        """Get threads where user participated"""
        try:
            # Find threads where user posted
            result = await self.db.execute(
                select(Message.thread_ts, func.count(Message.id).label('reply_count')).where(
                    and_(
                        Message.team_id == team_id,
                        Message.created_at >= cutoff_time,
                        Message.user_id == user_id,
                        Message.thread_ts.isnot(None)
                    )
                ).group_by(Message.thread_ts).order_by(desc('reply_count')).limit(5)
            )
            thread_stats = result.all()

            threads = []
            for thread_ts, reply_count in thread_stats:
                # Get the parent message
                parent_result = await self.db.execute(
                    select(Message).where(
                        and_(
                            Message.team_id == team_id,
                            Message.message_id == thread_ts
                        )
                    ).limit(1)
                )
                parent = parent_result.scalar_one_or_none()

                if parent:
                    threads.append({
                        'message_id': parent.message_id,
                        'channel_id': parent.channel_id,
                        'text': parent.text[:200] if parent.text else '',
                        'author': parent.user_id,
                        'user_replies': reply_count,
                        'created_at': parent.created_at.isoformat()
                    })

            return threads

        except Exception as e:
            logger.error("get_user_active_threads_error", error=str(e))
            return []

    def _generate_personalized_summary(self, digest: Dict[str, Any]) -> str:
        """Generate personalized summary text"""
        parts = []

        if digest.get('mentions'):
            parts.append(f"💬 You were mentioned {len(digest['mentions'])} times")

        if digest.get('my_threads'):
            parts.append(f"🧵 {len(digest['my_threads'])} active threads you're in")

        if digest.get('hot_topics'):
            top_topic = digest['hot_topics'][0]
            parts.append(f"🔥 Trending: {top_topic['entity']}")

        if digest.get('unresolved_questions'):
            parts.append(f"❓ {len(digest['unresolved_questions'])} questions need answers")

        return " | ".join(parts) if parts else "Quiet day in the workspace"

    async def format_for_slack(
        self,
        digest: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Format personalized digest as Slack blocks"""
        blocks = []

        # Header
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📬 Your Daily Digest",
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

        # Your Mentions (most important for user)
        if digest.get('mentions'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*💬 You Were Mentioned*"
                }
            })

            for mention in digest['mentions'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• {mention['text'][:150]}..."
                    }
                })

            blocks.append({"type": "divider"})

        # Your Active Threads
        if digest.get('my_threads'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🧵 Threads You're Following*"
                }
            })

            for thread in digest['my_threads'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• {thread['text'][:100]}...\n"
                               f"  You replied {thread['user_replies']} times"
                    }
                })

            blocks.append({"type": "divider"})

        # Hot Topics (from base digest)
        if digest.get('hot_topics'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🔥 Trending in Your Workspace*"
                }
            })

            for topic in digest['hot_topics'][:3]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• *{topic['entity']}* - {topic['mention_count']} mentions"
                    }
                })

            blocks.append({"type": "divider"})

        # Cross-Source Links
        if digest.get('cross_source_links'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🔗 New Slack ↔ GitHub Connections*"
                }
            })

            for link in digest['cross_source_links'][:2]:
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
        if digest.get('unresolved_questions'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*❓ Questions That Need Answers*"
                }
            })

            for q in digest['unresolved_questions'][:2]:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• {q['text'][:150]}..."
                    }
                })

            blocks.append({"type": "divider"})

        # Settings footer
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "💡 Personalized just for you • Type `/slackbot settings` to customize or mute"
            }]
        })

        return blocks

    async def send_digest_to_user(
        self,
        user_id: str,
        team_id: str,
        digest: Dict[str, Any]
    ) -> bool:
        """
        Send personalized digest to user via DM

        Args:
            user_id: Slack user ID
            team_id: Workspace team ID
            digest: Generated digest data

        Returns:
            True if successful
        """
        try:
            blocks = await self.format_for_slack(digest)

            # Send DM to user
            await slack_client_manager.post_message(
                team_id=team_id,
                channel_id=user_id,  # Sending to user ID opens DM
                text="Your Daily Digest",
                blocks=blocks
            )

            # Update last_digest_sent
            await self._update_last_sent(user_id, team_id)

            logger.info(
                "personalized_digest_sent",
                user_id=user_id,
                team_id=team_id
            )

            return True

        except Exception as e:
            logger.error(
                "send_personalized_digest_error",
                error=str(e),
                user_id=user_id,
                team_id=team_id
            )
            return False

    async def _update_last_sent(self, user_id: str, team_id: str):
        """Update last digest sent timestamp"""
        try:
            result = await self.db.execute(
                select(UserPreferences).where(
                    and_(
                        UserPreferences.user_id == user_id,
                        UserPreferences.team_id == team_id
                    )
                )
            )
            prefs = result.scalar_one_or_none()

            if prefs:
                prefs.last_digest_sent = datetime.utcnow()
                await self.db.commit()

        except Exception as e:
            logger.error("update_last_sent_error", error=str(e))

    async def send_digests_to_all_users(self, team_id: str) -> Dict[str, Any]:
        """
        Send digests to all users in a team who have it enabled

        Args:
            team_id: Workspace team ID

        Returns:
            Summary of sends (success/failed counts)
        """
        try:
            logger.info("sending_digests_to_all_users", team_id=team_id)

            # Get all users with digest enabled
            result = await self.db.execute(
                select(UserPreferences).where(
                    and_(
                        UserPreferences.team_id == team_id,
                        UserPreferences.daily_digest_enabled == True
                    )
                )
            )
            users_with_digest = result.scalars().all()

            if not users_with_digest:
                # No preferences yet, get active users from messages
                result = await self.db.execute(
                    select(Message.user_id).where(
                        and_(
                            Message.team_id == team_id,
                            Message.user_id.isnot(None)
                        )
                    ).distinct().limit(100)
                )
                user_ids = [row[0] for row in result.all()]
            else:
                user_ids = [prefs.user_id for prefs in users_with_digest]

            success_count = 0
            failed_count = 0
            skipped_count = 0

            for user_id in user_ids:
                try:
                    # Generate personalized digest
                    digest = await self.generate_personalized_digest(
                        user_id=user_id,
                        team_id=team_id
                    )

                    if not digest:
                        skipped_count += 1
                        continue

                    # Send it
                    sent = await self.send_digest_to_user(
                        user_id=user_id,
                        team_id=team_id,
                        digest=digest
                    )

                    if sent:
                        success_count += 1
                    else:
                        failed_count += 1

                except Exception as e:
                    logger.error(
                        "send_digest_to_user_failed",
                        error=str(e),
                        user_id=user_id
                    )
                    failed_count += 1

            logger.info(
                "bulk_digest_send_completed",
                team_id=team_id,
                success=success_count,
                failed=failed_count,
                skipped=skipped_count
            )

            return {
                'team_id': team_id,
                'success_count': success_count,
                'failed_count': failed_count,
                'skipped_count': skipped_count,
                'total_users': len(user_ids)
            }

        except Exception as e:
            logger.error("send_digests_to_all_users_error", error=str(e))
            return {
                'team_id': team_id,
                'success_count': 0,
                'failed_count': 0,
                'skipped_count': 0,
                'error': str(e)
            }


def get_personalized_digest_service(db: AsyncSession) -> PersonalizedDigestService:
    """Get personalized digest service instance"""
    return PersonalizedDigestService(db)
