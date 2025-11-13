"""
Slack Commands for Daily Digest Management

Provides slash commands and interactive buttons for users to manage their digest preferences.

Commands:
- /digest settings - View and update digest preferences
- /digest mute - Mute daily digest
- /digest unmute - Unmute daily digest
- /digest preview - See a preview of today's digest
"""
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import get_logger
from app.models.user_preferences import UserPreferences
from app.services.personalized_digest_service import PersonalizedDigestService
from app.services.slack_client import slack_client_manager

logger = get_logger(__name__)


class DigestCommandHandler:
    """Handles digest-related slash commands"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def handle_digest_command(
        self,
        command: str,
        user_id: str,
        team_id: str,
        channel_id: str
    ) -> Dict[str, Any]:
        """
        Handle /digest command with subcommands

        Args:
            command: Full command text (e.g., "settings", "mute", "preview")
            user_id: Slack user ID
            team_id: Slack team ID
            channel_id: Channel ID where command was invoked

        Returns:
            Slack message response
        """
        try:
            # Parse subcommand
            parts = command.strip().split()
            subcommand = parts[0].lower() if parts else "help"

            if subcommand == "settings":
                return await self._handle_settings(user_id, team_id)
            elif subcommand == "mute":
                return await self._handle_mute(user_id, team_id)
            elif subcommand == "unmute":
                return await self._handle_unmute(user_id, team_id)
            elif subcommand == "preview":
                return await self._handle_preview(user_id, team_id)
            else:
                return self._get_help_message()

        except Exception as e:
            logger.error("digest_command_error", error=str(e), user_id=user_id)
            return {
                "response_type": "ephemeral",
                "text": f"Error processing command: {str(e)}"
            }

    async def _handle_settings(
        self,
        user_id: str,
        team_id: str
    ) -> Dict[str, Any]:
        """Show current settings with interactive buttons"""
        try:
            # Get current preferences
            result = await self.db.execute(
                select(UserPreferences).where(
                    UserPreferences.user_id == user_id,
                    UserPreferences.team_id == team_id
                )
            )
            prefs = result.scalar_one_or_none()

            if not prefs:
                prefs = UserPreferences(user_id=user_id, team_id=team_id)

            # Build settings message
            blocks = self._build_settings_blocks(prefs)

            return {
                "response_type": "ephemeral",
                "blocks": blocks
            }

        except Exception as e:
            logger.error("handle_settings_error", error=str(e))
            raise

    async def _handle_mute(
        self,
        user_id: str,
        team_id: str
    ) -> Dict[str, Any]:
        """Mute daily digest"""
        try:
            result = await self.db.execute(
                select(UserPreferences).where(
                    UserPreferences.user_id == user_id,
                    UserPreferences.team_id == team_id
                )
            )
            prefs = result.scalar_one_or_none()

            if not prefs:
                prefs = UserPreferences(user_id=user_id, team_id=team_id)
                self.db.add(prefs)

            prefs.daily_digest_enabled = False
            await self.db.commit()

            logger.info("digest_muted_via_command", user_id=user_id)

            return {
                "response_type": "ephemeral",
                "text": "✓ Daily digest muted. You won't receive automatic digests anymore.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "✓ *Daily digest muted*\n\nYou won't receive automatic digests anymore.\n\nTo re-enable, use `/digest unmute`"
                        }
                    }
                ]
            }

        except Exception as e:
            logger.error("handle_mute_error", error=str(e))
            raise

    async def _handle_unmute(
        self,
        user_id: str,
        team_id: str
    ) -> Dict[str, Any]:
        """Unmute daily digest"""
        try:
            result = await self.db.execute(
                select(UserPreferences).where(
                    UserPreferences.user_id == user_id,
                    UserPreferences.team_id == team_id
                )
            )
            prefs = result.scalar_one_or_none()

            if not prefs:
                prefs = UserPreferences(user_id=user_id, team_id=team_id)
                self.db.add(prefs)

            prefs.daily_digest_enabled = True
            await self.db.commit()

            logger.info("digest_unmuted_via_command", user_id=user_id)

            return {
                "response_type": "ephemeral",
                "text": "✓ Daily digest enabled. You'll receive digests at 9:00 AM.",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "✓ *Daily digest enabled*\n\nYou'll receive personalized digests at 9:00 AM daily.\n\nTo customize what you receive, use `/digest settings`"
                        }
                    }
                ]
            }

        except Exception as e:
            logger.error("handle_unmute_error", error=str(e))
            raise

    async def _handle_preview(
        self,
        user_id: str,
        team_id: str
    ) -> Dict[str, Any]:
        """Show preview of digest"""
        try:
            digest_service = PersonalizedDigestService(self.db)

            # Generate digest
            digest = await digest_service.generate_personalized_digest(
                user_id=user_id,
                team_id=team_id,
                hours_back=24
            )

            if not digest:
                return {
                    "response_type": "ephemeral",
                    "text": "No digest available. Either you have digests muted or there's no recent activity."
                }

            # Return the digest blocks
            blocks = digest.get('slack_blocks', [])

            # Add header
            blocks.insert(0, {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Preview of Your Daily Digest*\n\nThis is what you would receive in your DM:"
                }
            })
            blocks.insert(1, {"type": "divider"})

            return {
                "response_type": "ephemeral",
                "blocks": blocks
            }

        except Exception as e:
            logger.error("handle_preview_error", error=str(e))
            raise

    def _get_help_message(self) -> Dict[str, Any]:
        """Get help message for digest commands"""
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Daily Digest Commands*\n\nManage your personalized daily digest:"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "• `/digest settings` - View and update your digest preferences\n"
                                "• `/digest mute` - Stop receiving daily digests\n"
                                "• `/digest unmute` - Resume receiving daily digests\n"
                                "• `/digest preview` - See a preview of today's digest"
                    }
                }
            ]
        }

    def _build_settings_blocks(self, prefs: UserPreferences) -> List[Dict[str, Any]]:
        """Build interactive settings blocks"""
        status_emoji = "✓" if prefs.daily_digest_enabled else "✗"
        status_text = "Enabled" if prefs.daily_digest_enabled else "Muted"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Your Daily Digest Settings*\n\nStatus: {status_emoji} *{status_text}*"
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*What's included in your digest:*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self._format_preferences(prefs)
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Quick Actions:*"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Mute Digest"
                        },
                        "style": "danger" if prefs.daily_digest_enabled else "primary",
                        "value": "mute_digest",
                        "action_id": "digest_mute"
                    } if prefs.daily_digest_enabled else {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Unmute Digest"
                        },
                        "style": "primary",
                        "value": "unmute_digest",
                        "action_id": "digest_unmute"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Preview Digest"
                        },
                        "value": "preview_digest",
                        "action_id": "digest_preview"
                    }
                ]
            }
        ]

        return blocks

    def _format_preferences(self, prefs: UserPreferences) -> str:
        """Format preferences as readable text"""
        items = []

        if prefs.include_hot_topics:
            items.append("• Hot Topics (trending discussions)")
        if prefs.include_trending_discussions:
            items.append("• Trending Discussions (high engagement)")
        if prefs.include_mentions:
            items.append("• Your Mentions")
        if prefs.include_my_threads:
            items.append("• Your Active Threads")
        if prefs.include_expert_insights:
            items.append("• Expert Insights")
        if prefs.include_unanswered_questions:
            items.append("• Unanswered Questions")

        if not items:
            items.append("• Nothing selected")

        filters = []
        if prefs.only_my_channels:
            filters.append("• Only from channels you're in")
        if prefs.min_priority_score > 0:
            filters.append(f"• Minimum priority: {prefs.min_priority_score:.1f}")

        result = "\n".join(items)
        if filters:
            result += "\n\n*Filters:*\n" + "\n".join(filters)

        result += f"\n\n*Delivery:* {prefs.digest_frequency.capitalize()} at {prefs.digest_time_hour:02d}:00"

        return result


async def handle_digest_interaction(
    action_id: str,
    user_id: str,
    team_id: str,
    db: AsyncSession
) -> Dict[str, Any]:
    """
    Handle interactive button clicks for digest settings

    Args:
        action_id: Button action ID (digest_mute, digest_unmute, digest_preview)
        user_id: Slack user ID
        team_id: Slack team ID
        db: Database session

    Returns:
        Slack message response
    """
    handler = DigestCommandHandler(db)

    if action_id == "digest_mute":
        return await handler._handle_mute(user_id, team_id)
    elif action_id == "digest_unmute":
        return await handler._handle_unmute(user_id, team_id)
    elif action_id == "digest_preview":
        return await handler._handle_preview(user_id, team_id)
    else:
        return {
            "response_type": "ephemeral",
            "text": "Unknown action"
        }
