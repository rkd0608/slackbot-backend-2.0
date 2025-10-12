"""Notification orchestration service"""
import httpx
from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workspace import Workspace
from app.services.email_service import email_service
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class NotificationService:
    """Orchestrates notifications via email and Slack DM"""

    async def send_trial_expiration_warning(
        self,
        workspace: Workspace,
        days_remaining: int,
        db: AsyncSession
    ) -> None:
        """Send trial expiration warning via email and Slack"""

        try:
            # Send email if installer has email
            if workspace.installer_email:
                await email_service.send_trial_expiration_warning(
                    recipient_email=workspace.installer_email,
                    team_name=workspace.team_name,
                    days_remaining=days_remaining,
                    tier=workspace.subscription_tier,
                    query_limit=workspace.monthly_query_limit
                )

            # Send Slack DM to installer
            await self._send_slack_dm(
                bot_token=workspace.bot_access_token,
                user_id=workspace.installer_user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"⏰ Trial expires in {days_remaining} days"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Your *{workspace.team_name}* workspace trial will expire in *{days_remaining} days*.\n\n"
                                   f"You're on the *{workspace.subscription_tier.upper()}* tier with {workspace.monthly_query_limit:,} queries/month."
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Subscribe now to keep using:\n• 🔍 AI-powered search\n• 💬 Smart Q&A with citations\n• 📊 Analytics & insights"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Subscribe Now"
                                },
                                "style": "primary",
                                "url": f"https://your-app.com/subscribe?team={workspace.team_id}"
                            },
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "View Pricing"
                                },
                                "url": "https://your-app.com/pricing"
                            }
                        ]
                    }
                ]
            )

            logger.info(
                "trial_warning_sent",
                team_id=workspace.team_id,
                days_remaining=days_remaining
            )

        except Exception as e:
            logger.error(
                "trial_warning_failed",
                error=str(e),
                team_id=workspace.team_id
            )

    async def send_trial_expired_notification(
        self,
        workspace: Workspace,
        db: AsyncSession
    ) -> None:
        """Send trial expired notification"""

        try:
            # Send email
            if workspace.installer_email:
                await email_service.send_trial_expired_notification(
                    recipient_email=workspace.installer_email,
                    team_name=workspace.team_name,
                    tier=workspace.subscription_tier
                )

            # Send Slack DM
            await self._send_slack_dm(
                bot_token=workspace.bot_access_token,
                user_id=workspace.installer_user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "⚠️ Trial Expired",
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Your *{workspace.team_name}* workspace trial has ended.\n\n"
                                   "Your data is safe! Subscribe now to restore access."
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Activate Subscription"
                                },
                                "style": "primary",
                                "url": f"https://your-app.com/subscribe?team={workspace.team_id}"
                            }
                        ]
                    }
                ]
            )

            logger.info("trial_expired_notification_sent", team_id=workspace.team_id)

        except Exception as e:
            logger.error(
                "trial_expired_notification_failed",
                error=str(e),
                team_id=workspace.team_id
            )

    async def send_payment_failed_notification(
        self,
        workspace: Workspace,
        amount: float,
        retry_date: Optional[str] = None,
        db: AsyncSession = None
    ) -> None:
        """Send payment failure notification"""

        try:
            # Send email
            if workspace.installer_email:
                await email_service.send_payment_failed_notification(
                    recipient_email=workspace.installer_email,
                    team_name=workspace.team_name,
                    amount=amount,
                    retry_date=retry_date
                )

            # Send Slack DM
            await self._send_slack_dm(
                bot_token=workspace.bot_access_token,
                user_id=workspace.installer_user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "⚠️ Payment Failed"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"We couldn't process your payment of *${amount:.2f}* for {workspace.team_name}.\n\n"
                                   "⚠️ Your subscription will be suspended if not updated."
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Common reasons:*\n• Expired card\n• Insufficient funds\n• Card declined by bank"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Update Payment Method"
                                },
                                "style": "danger",
                                "url": "https://your-app.com/billing"
                            }
                        ]
                    }
                ]
            )

            logger.info(
                "payment_failed_notification_sent",
                team_id=workspace.team_id,
                amount=amount
            )

        except Exception as e:
            logger.error(
                "payment_failed_notification_error",
                error=str(e),
                team_id=workspace.team_id
            )

    async def send_query_limit_warning(
        self,
        workspace: Workspace,
        percentage: float,
        db: AsyncSession
    ) -> None:
        """Send query limit warning"""

        try:
            # Send email
            if workspace.installer_email:
                await email_service.send_query_limit_warning(
                    recipient_email=workspace.installer_email,
                    team_name=workspace.team_name,
                    queries_used=workspace.queries_used_this_month,
                    query_limit=workspace.monthly_query_limit,
                    percentage=percentage,
                    tier=workspace.subscription_tier
                )

            # Determine urgency for Slack message
            if percentage >= 100:
                emoji = "🚫"
                status = "Limit Reached"
                message = f"You've used all {workspace.monthly_query_limit:,} queries this month. Queries are now blocked."
                button_style = "danger"
            elif percentage >= 90:
                emoji = "⚠️"
                status = "90% Used"
                message = f"You've used {workspace.queries_used_this_month:,} of {workspace.monthly_query_limit:,} queries ({percentage:.0f}%).\n\nQueries will be blocked at 100%."
                button_style = "danger"
            else:  # 80%
                emoji = "📊"
                status = "80% Used"
                message = f"You've used {workspace.queries_used_this_month:,} of {workspace.monthly_query_limit:,} queries ({percentage:.0f}%)."
                button_style = "primary"

            # Send Slack DM
            await self._send_slack_dm(
                bot_token=workspace.bot_access_token,
                user_id=workspace.installer_user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{emoji} Query Usage: {status}"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": message
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Current plan: *{workspace.subscription_tier.upper()}* tier\nYour limit resets at the start of next billing cycle."
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Upgrade Plan"
                                },
                                "style": button_style,
                                "url": "https://your-app.com/upgrade"
                            },
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "View Usage"
                                },
                                "url": "https://your-app.com/dashboard"
                            }
                        ]
                    }
                ]
            )

            logger.info(
                "query_limit_warning_sent",
                team_id=workspace.team_id,
                percentage=percentage
            )

        except Exception as e:
            logger.error(
                "query_limit_warning_failed",
                error=str(e),
                team_id=workspace.team_id
            )

    async def send_subscription_activated(
        self,
        workspace: Workspace,
        amount: float,
        db: AsyncSession
    ) -> None:
        """Send subscription activated confirmation"""

        try:
            # Send email
            if workspace.installer_email:
                await email_service.send_subscription_activated(
                    recipient_email=workspace.installer_email,
                    team_name=workspace.team_name,
                    tier=workspace.subscription_tier,
                    query_limit=workspace.monthly_query_limit,
                    amount=amount
                )

            # Send Slack DM
            await self._send_slack_dm(
                bot_token=workspace.bot_access_token,
                user_id=workspace.installer_user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"🎉 Welcome to {workspace.subscription_tier.title()}!",
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Your subscription is now active for *{workspace.team_name}*!\n\n"
                                   f"✅ {workspace.monthly_query_limit:,} queries/month\n"
                                   f"✅ AI-powered search\n"
                                   f"✅ Smart Q&A with citations\n"
                                   f"✅ Advanced analytics"
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Monthly charge:*\n${amount:.2f}"
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Plan:*\n{workspace.subscription_tier.title()}"
                            }
                        ]
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Get started:*\n• `/ask [question]` - Ask anything\n• `/find [term]` - Search messages\n• `@AI Assistant` - Mention me"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "View Dashboard"
                                },
                                "style": "primary",
                                "url": "https://your-app.com/dashboard"
                            }
                        ]
                    }
                ]
            )

            logger.info(
                "subscription_activated_notification_sent",
                team_id=workspace.team_id
            )

        except Exception as e:
            logger.error(
                "subscription_activated_notification_failed",
                error=str(e),
                team_id=workspace.team_id
            )

    async def _send_slack_dm(
        self,
        bot_token: str,
        user_id: str,
        blocks: list
    ) -> bool:
        """Send Slack DM using chat.postMessage"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Open DM channel
                dm_response = await client.post(
                    "https://slack.com/api/conversations.open",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={"users": user_id}
                )

                dm_data = dm_response.json()
                if not dm_data.get("ok"):
                    logger.error("slack_dm_open_failed", error=dm_data.get("error"))
                    return False

                channel_id = dm_data["channel"]["id"]

                # Send message
                msg_response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={
                        "channel": channel_id,
                        "blocks": blocks,
                        "text": blocks[0]["text"]["text"] if blocks else "Notification"
                    }
                )

                msg_data = msg_response.json()
                if not msg_data.get("ok"):
                    logger.error("slack_dm_send_failed", error=msg_data.get("error"))
                    return False

                return True

        except Exception as e:
            logger.error("slack_dm_error", error=str(e), user_id=user_id)
            return False


# Global notification service instance
notification_service = NotificationService()
