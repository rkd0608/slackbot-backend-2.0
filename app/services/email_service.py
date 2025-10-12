"""Email notification service using AWS SES"""
import boto3
from botocore.exceptions import ClientError
from typing import Dict, List, Optional
from datetime import datetime
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailService:
    """Handles email notifications via AWS SES"""

    def __init__(self):
        self.ses_client = boto3.client(
            'ses',
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key
        )
        self.sender = settings.email_sender_address
        self.sender_name = settings.email_sender_name

    async def send_trial_expiration_warning(
        self,
        recipient_email: str,
        team_name: str,
        days_remaining: int,
        tier: str,
        query_limit: int
    ) -> bool:
        """Send trial expiration warning email"""

        subject = f"⏰ Your {team_name} trial expires in {days_remaining} days"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #611f69; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .cta-button {{
                    display: inline-block;
                    background: #611f69;
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin: 20px 0;
                }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
                .tier-badge {{
                    display: inline-block;
                    background: #36c5f0;
                    color: white;
                    padding: 4px 12px;
                    border-radius: 12px;
                    font-size: 14px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>⏰ Trial Ending Soon</h1>
                </div>
                <div class="content">
                    <h2>Hi there,</h2>
                    <p>
                        Your <strong>{team_name}</strong> workspace trial of our Slack AI Assistant
                        will expire in <strong>{days_remaining} days</strong>.
                    </p>

                    <p>You're currently on the <span class="tier-badge">{tier.upper()}</span> tier with:</p>
                    <ul>
                        <li>✅ {query_limit:,} queries per month</li>
                        <li>✅ AI-powered search across all channels</li>
                        <li>✅ Smart Q&A with citations</li>
                        <li>✅ Advanced analytics</li>
                    </ul>

                    <p>
                        To continue using these features after your trial, subscribe now:
                    </p>

                    <center>
                        <a href="https://your-app.com/subscribe?team={team_name}" class="cta-button">
                            Subscribe Now
                        </a>
                    </center>

                    <p style="margin-top: 30px; font-size: 14px; color: #666;">
                        Questions? Reply to this email or visit our
                        <a href="https://your-app.com/support">support page</a>.
                    </p>
                </div>
                <div class="footer">
                    <p>Slack AI Assistant | your-company.com</p>
                    <p>
                        <a href="https://your-app.com/unsubscribe">Unsubscribe</a> |
                        <a href="https://your-app.com/privacy">Privacy Policy</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
        Trial Ending Soon

        Hi there,

        Your {team_name} workspace trial expires in {days_remaining} days.

        You're currently on the {tier.upper()} tier with {query_limit:,} queries per month.

        Subscribe now to continue using:
        - AI-powered search across all channels
        - Smart Q&A with citations
        - Advanced analytics

        Subscribe here: https://your-app.com/subscribe?team={team_name}

        Questions? Visit https://your-app.com/support
        """

        return await self._send_email(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )

    async def send_trial_expired_notification(
        self,
        recipient_email: str,
        team_name: str,
        tier: str
    ) -> bool:
        """Send trial expired notification"""

        subject = f"⚠️ Your {team_name} trial has expired"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #e01e5a; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .cta-button {{
                    display: inline-block;
                    background: #611f69;
                    color: white;
                    padding: 15px 40px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin: 20px 0;
                    font-size: 18px;
                    font-weight: bold;
                }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>⚠️ Trial Expired</h1>
                </div>
                <div class="content">
                    <h2>Hi there,</h2>
                    <p>
                        Your <strong>{team_name}</strong> workspace trial has ended.
                    </p>

                    <p>
                        Don't worry - your data is safe! Subscribe now to restore access to:
                    </p>
                    <ul>
                        <li>🔍 AI-powered search across all your Slack channels</li>
                        <li>💬 Instant answers with source citations</li>
                        <li>📊 Usage analytics and insights</li>
                        <li>⚡ Fast indexing of new messages</li>
                    </ul>

                    <center>
                        <a href="https://your-app.com/subscribe?team={team_name}" class="cta-button">
                            Activate Subscription
                        </a>
                    </center>

                    <p style="margin-top: 30px; font-size: 14px; color: #666;">
                        Need help choosing a plan? <a href="https://your-app.com/pricing">View pricing</a>
                        or <a href="mailto:support@your-app.com">contact support</a>.
                    </p>
                </div>
                <div class="footer">
                    <p>Slack AI Assistant</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
        Trial Expired

        Hi there,

        Your {team_name} workspace trial has ended.

        Subscribe now to restore access to AI-powered search, instant answers, and analytics.

        Activate subscription: https://your-app.com/subscribe?team={team_name}

        View pricing: https://your-app.com/pricing
        Contact support: support@your-app.com
        """

        return await self._send_email(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )

    async def send_payment_failed_notification(
        self,
        recipient_email: str,
        team_name: str,
        amount: float,
        retry_date: Optional[str] = None
    ) -> bool:
        """Send payment failure notification"""

        subject = f"⚠️ Payment failed for {team_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #e01e5a; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .warning-box {{
                    background: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                }}
                .cta-button {{
                    display: inline-block;
                    background: #611f69;
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin: 20px 0;
                }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>⚠️ Payment Failed</h1>
                </div>
                <div class="content">
                    <h2>Action Required</h2>
                    <p>
                        We were unable to process the payment of <strong>${amount:.2f}</strong>
                        for your <strong>{team_name}</strong> workspace.
                    </p>

                    <div class="warning-box">
                        <strong>⚠️ Your subscription will be suspended if payment is not updated.</strong>
                    </div>

                    <p>Common reasons for payment failures:</p>
                    <ul>
                        <li>Expired or invalid payment method</li>
                        <li>Insufficient funds</li>
                        <li>Card declined by bank</li>
                    </ul>

                    <p>Update your payment method now to avoid service interruption:</p>

                    <center>
                        <a href="https://your-app.com/billing" class="cta-button">
                            Update Payment Method
                        </a>
                    </center>

                    {f'<p style="font-size: 14px; color: #666;">We will automatically retry on {retry_date}.</p>' if retry_date else ''}
                </div>
                <div class="footer">
                    <p>Slack AI Assistant | Questions? support@your-app.com</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
        Payment Failed - Action Required

        We were unable to process the payment of ${amount:.2f} for {team_name}.

        Update your payment method now: https://your-app.com/billing

        Questions? Contact support@your-app.com
        """

        return await self._send_email(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )

    async def send_query_limit_warning(
        self,
        recipient_email: str,
        team_name: str,
        queries_used: int,
        query_limit: int,
        percentage: float,
        tier: str
    ) -> bool:
        """Send query limit warning"""

        subject = f"⚠️ {team_name} has used {percentage:.0f}% of monthly queries"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #2eb67d; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .usage-bar {{
                    background: #e0e0e0;
                    height: 30px;
                    border-radius: 15px;
                    overflow: hidden;
                    margin: 20px 0;
                }}
                .usage-fill {{
                    background: {'#e01e5a' if percentage >= 90 else '#ffc107' if percentage >= 80 else '#2eb67d'};
                    height: 100%;
                    width: {percentage}%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-weight: bold;
                }}
                .cta-button {{
                    display: inline-block;
                    background: #611f69;
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin: 20px 0;
                }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📊 Query Usage Alert</h1>
                </div>
                <div class="content">
                    <h2>Hi there,</h2>
                    <p>
                        Your <strong>{team_name}</strong> workspace has used
                        <strong>{queries_used:,} of {query_limit:,}</strong> queries this month.
                    </p>

                    <div class="usage-bar">
                        <div class="usage-fill">{percentage:.0f}%</div>
                    </div>

                    <p>You're currently on the <strong>{tier.upper()}</strong> tier.</p>

                    <p>
                        {'<strong>⚠️ Queries will be blocked when you reach 100%.</strong>' if percentage >= 90 else 'Consider upgrading to a higher tier for more queries.'}
                    </p>

                    <center>
                        <a href="https://your-app.com/upgrade" class="cta-button">
                            Upgrade Plan
                        </a>
                    </center>

                    <p style="margin-top: 30px; font-size: 14px; color: #666;">
                        Your query limit will reset at the start of your next billing cycle.
                    </p>
                </div>
                <div class="footer">
                    <p>Slack AI Assistant</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
        Query Usage Alert

        Your {team_name} workspace has used {queries_used:,} of {query_limit:,} queries ({percentage:.0f}%).

        {'Queries will be blocked at 100%. Upgrade now: https://your-app.com/upgrade' if percentage >= 90 else 'Consider upgrading: https://your-app.com/upgrade'}
        """

        return await self._send_email(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )

    async def send_subscription_activated(
        self,
        recipient_email: str,
        team_name: str,
        tier: str,
        query_limit: int,
        amount: float
    ) -> bool:
        """Send subscription activated confirmation"""

        subject = f"🎉 Welcome to {tier.title()} - {team_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #2eb67d; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .success-box {{
                    background: #d4edda;
                    border-left: 4px solid #2eb67d;
                    padding: 15px;
                    margin: 20px 0;
                }}
                .cta-button {{
                    display: inline-block;
                    background: #611f69;
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 4px;
                    margin: 20px 0;
                }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎉 Subscription Activated!</h1>
                </div>
                <div class="content">
                    <h2>Welcome to {tier.title()}, {team_name}!</h2>

                    <div class="success-box">
                        ✅ Your subscription is now active
                    </div>

                    <p>Your plan includes:</p>
                    <ul>
                        <li>✅ {query_limit:,} queries per month</li>
                        <li>✅ AI-powered search across all channels</li>
                        <li>✅ Smart Q&A with citations</li>
                        <li>✅ Priority indexing</li>
                        <li>✅ Advanced analytics</li>
                    </ul>

                    <p><strong>Monthly charge:</strong> ${amount:.2f}</p>

                    <p>Start using your AI assistant in Slack:</p>
                    <ul>
                        <li>Type <code>/ask [your question]</code></li>
                        <li>Type <code>/find [search term]</code></li>
                        <li>Mention <code>@AI Assistant</code> in any channel</li>
                    </ul>

                    <center>
                        <a href="https://your-app.com/dashboard" class="cta-button">
                            View Dashboard
                        </a>
                    </center>
                </div>
                <div class="footer">
                    <p>Slack AI Assistant | <a href="https://your-app.com/billing">Manage Subscription</a></p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
        Subscription Activated!

        Welcome to {tier.title()}, {team_name}!

        Your plan includes {query_limit:,} queries per month for ${amount:.2f}/month.

        Start using in Slack:
        - /ask [your question]
        - /find [search term]
        - @AI Assistant

        View dashboard: https://your-app.com/dashboard
        """

        return await self._send_email(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )

    async def _send_email(
        self,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str
    ) -> bool:
        """Send email via AWS SES"""

        try:
            response = self.ses_client.send_email(
                Source=f"{self.sender_name} <{self.sender}>",
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {
                        'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                        'Html': {'Data': html_body, 'Charset': 'UTF-8'}
                    }
                }
            )

            logger.info(
                "email_sent",
                recipient=recipient,
                subject=subject,
                message_id=response['MessageId']
            )
            return True

        except ClientError as e:
            logger.error(
                "email_send_failed",
                recipient=recipient,
                error=str(e),
                error_code=e.response['Error']['Code']
            )
            return False
        except Exception as e:
            logger.error(
                "email_send_error",
                recipient=recipient,
                error=str(e)
            )
            return False


# Global email service instance
email_service = EmailService()
