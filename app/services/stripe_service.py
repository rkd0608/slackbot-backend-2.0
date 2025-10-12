"""Stripe subscription management service"""
import stripe
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workspace import Workspace, InstallationLog
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StripeService:
    """Handles Stripe subscription and payment operations"""

    def __init__(self):
        stripe.api_key = settings.stripe_secret_key
        self.webhook_secret = settings.stripe_webhook_secret

        # Pricing configuration - Price IDs from Stripe Dashboard
        self.price_ids = {
            "starter": settings.stripe_price_starter,
            "growth": settings.stripe_price_growth,
            "business": settings.stripe_price_business,
            "enterprise": settings.stripe_price_enterprise
        }

    async def create_customer(
        self,
        team_id: str,
        team_name: str,
        installer_email: Optional[str],
        db: AsyncSession
    ) -> str:
        """Create Stripe customer for workspace"""

        try:
            customer = stripe.Customer.create(
                email=installer_email,
                name=team_name,
                metadata={
                    "team_id": team_id,
                    "team_name": team_name
                }
            )

            # Update workspace with customer ID
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if workspace:
                workspace.stripe_customer_id = customer.id
                await db.commit()

            logger.info(
                "stripe_customer_created",
                team_id=team_id,
                customer_id=customer.id
            )

            return customer.id

        except Exception as e:
            logger.error(
                "create_stripe_customer_error",
                error=str(e),
                team_id=team_id
            )
            raise

    async def create_checkout_session(
        self,
        team_id: str,
        tier: str,
        db: AsyncSession,
        success_url: str,
        cancel_url: str
    ) -> Dict[str, str]:
        """Create Stripe Checkout session for subscription"""

        try:
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                raise ValueError(f"Workspace not found: {team_id}")

            # Get or create Stripe customer
            customer_id = workspace.stripe_customer_id
            if not customer_id:
                customer_id = await self.create_customer(
                    team_id=team_id,
                    team_name=workspace.team_name,
                    installer_email=workspace.installer_email,
                    db=db
                )

            # Get price ID for tier
            price_id = self.price_ids.get(tier)
            if not price_id:
                raise ValueError(f"Invalid tier: {tier}")

            # Create checkout session
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                line_items=[
                    {
                        "price": price_id,
                        "quantity": 1
                    }
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "team_id": team_id,
                    "tier": tier
                },
                subscription_data={
                    "metadata": {
                        "team_id": team_id,
                        "tier": tier
                    }
                }
            )

            logger.info(
                "checkout_session_created",
                team_id=team_id,
                session_id=session.id,
                tier=tier
            )

            return {
                "checkout_url": session.url,
                "session_id": session.id
            }

        except Exception as e:
            logger.error(
                "create_checkout_session_error",
                error=str(e),
                team_id=team_id
            )
            raise

    async def create_billing_portal_session(
        self,
        team_id: str,
        db: AsyncSession,
        return_url: str
    ) -> Dict[str, str]:
        """Create Stripe billing portal session"""

        try:
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                raise ValueError(f"Workspace not found: {team_id}")

            if not workspace.stripe_customer_id:
                raise ValueError(f"No Stripe customer for workspace: {team_id}")

            # Create portal session
            session = stripe.billing_portal.Session.create(
                customer=workspace.stripe_customer_id,
                return_url=return_url
            )

            logger.info(
                "billing_portal_session_created",
                team_id=team_id,
                customer_id=workspace.stripe_customer_id
            )

            return {
                "portal_url": session.url
            }

        except Exception as e:
            logger.error(
                "create_billing_portal_error",
                error=str(e),
                team_id=team_id
            )
            raise

    async def handle_subscription_created(
        self,
        subscription: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Handle subscription.created webhook event"""

        try:
            team_id = subscription["metadata"].get("team_id")
            tier = subscription["metadata"].get("tier")

            if not team_id:
                logger.warning("subscription_created_missing_team_id", subscription_id=subscription["id"])
                return

            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found", team_id=team_id)
                return

            # Update workspace
            workspace.stripe_subscription_id = subscription["id"]
            workspace.subscription_status = "active"
            workspace.subscription_tier = tier or workspace.subscription_tier
            workspace.current_period_start = datetime.fromtimestamp(subscription["current_period_start"])
            workspace.current_period_end = datetime.fromtimestamp(subscription["current_period_end"])
            workspace.updated_at = datetime.utcnow()

            # Get query limit for tier
            from app.services.oauth_service import oauth_service
            workspace.monthly_query_limit = oauth_service.get_query_limit(workspace.subscription_tier)

            # Log subscription event
            log = InstallationLog(
                team_id=team_id,
                event_type="subscription_activated",
                event_data={
                    "subscription_id": subscription["id"],
                    "tier": workspace.subscription_tier,
                    "period_start": workspace.current_period_start.isoformat(),
                    "period_end": workspace.current_period_end.isoformat()
                },
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()

            logger.info(
                "subscription_activated",
                team_id=team_id,
                subscription_id=subscription["id"],
                tier=workspace.subscription_tier
            )

            # Send activation notification
            from app.services.notification_service import notification_service
            try:
                # Get first invoice amount
                amount = subscription.get("plan", {}).get("amount", 0) / 100 if subscription.get("plan") else 0
                await notification_service.send_subscription_activated(
                    workspace=workspace,
                    amount=amount,
                    db=db
                )
            except Exception as notify_error:
                logger.error("subscription_notification_failed", error=str(notify_error))

        except Exception as e:
            logger.error(
                "handle_subscription_created_error",
                error=str(e),
                subscription_id=subscription.get("id")
            )
            raise

    async def handle_subscription_updated(
        self,
        subscription: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Handle subscription.updated webhook event"""

        try:
            team_id = subscription["metadata"].get("team_id")

            if not team_id:
                logger.warning("subscription_updated_missing_team_id", subscription_id=subscription["id"])
                return

            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found", team_id=team_id)
                return

            # Update subscription details
            workspace.current_period_start = datetime.fromtimestamp(subscription["current_period_start"])
            workspace.current_period_end = datetime.fromtimestamp(subscription["current_period_end"])
            workspace.updated_at = datetime.utcnow()

            # Check for status changes
            status = subscription["status"]
            if status in ["active", "trialing"]:
                workspace.subscription_status = "active"
            elif status in ["past_due", "unpaid"]:
                workspace.subscription_status = "past_due"
            elif status in ["canceled", "incomplete_expired"]:
                workspace.subscription_status = "cancelled"

            await db.commit()

            logger.info(
                "subscription_updated",
                team_id=team_id,
                subscription_id=subscription["id"],
                status=status
            )

        except Exception as e:
            logger.error(
                "handle_subscription_updated_error",
                error=str(e),
                subscription_id=subscription.get("id")
            )
            raise

    async def handle_subscription_deleted(
        self,
        subscription: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Handle subscription.deleted webhook event"""

        try:
            team_id = subscription["metadata"].get("team_id")

            if not team_id:
                logger.warning("subscription_deleted_missing_team_id", subscription_id=subscription["id"])
                return

            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found", team_id=team_id)
                return

            # Update workspace status
            workspace.subscription_status = "cancelled"
            workspace.cancelled_at = datetime.utcnow()
            workspace.updated_at = datetime.utcnow()

            # Log cancellation
            log = InstallationLog(
                team_id=team_id,
                event_type="subscription_cancelled",
                event_data={
                    "subscription_id": subscription["id"],
                    "cancelled_at": workspace.cancelled_at.isoformat()
                },
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()

            logger.info(
                "subscription_cancelled",
                team_id=team_id,
                subscription_id=subscription["id"]
            )

        except Exception as e:
            logger.error(
                "handle_subscription_deleted_error",
                error=str(e),
                subscription_id=subscription.get("id")
            )
            raise

    async def handle_payment_succeeded(
        self,
        invoice: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Handle invoice.payment_succeeded webhook event"""

        try:
            subscription_id = invoice.get("subscription")
            if not subscription_id:
                return

            # Get subscription to find team_id
            subscription = stripe.Subscription.retrieve(subscription_id)
            team_id = subscription["metadata"].get("team_id")

            if not team_id:
                return

            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found", team_id=team_id)
                return

            # Update last payment info
            workspace.last_payment_date = datetime.fromtimestamp(invoice["created"])
            workspace.last_payment_amount = invoice["amount_paid"] / 100  # Convert from cents

            # If subscription was past_due, reactivate it
            if workspace.subscription_status == "past_due":
                workspace.subscription_status = "active"

            # Reset monthly query count if new billing period
            period_start = datetime.fromtimestamp(subscription["current_period_start"])
            if workspace.current_period_start != period_start:
                workspace.queries_used_this_month = 0

            workspace.updated_at = datetime.utcnow()

            await db.commit()

            logger.info(
                "payment_succeeded",
                team_id=team_id,
                invoice_id=invoice["id"],
                amount=workspace.last_payment_amount
            )

        except Exception as e:
            logger.error(
                "handle_payment_succeeded_error",
                error=str(e),
                invoice_id=invoice.get("id")
            )
            raise

    async def handle_payment_failed(
        self,
        invoice: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Handle invoice.payment_failed webhook event"""

        try:
            subscription_id = invoice.get("subscription")
            if not subscription_id:
                return

            # Get subscription to find team_id
            subscription = stripe.Subscription.retrieve(subscription_id)
            team_id = subscription["metadata"].get("team_id")

            if not team_id:
                return

            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found", team_id=team_id)
                return

            # Update status to past_due
            workspace.subscription_status = "past_due"
            workspace.updated_at = datetime.utcnow()

            # Log payment failure
            log = InstallationLog(
                team_id=team_id,
                event_type="payment_failed",
                event_data={
                    "invoice_id": invoice["id"],
                    "amount": invoice["amount_due"] / 100,
                    "attempt_count": invoice.get("attempt_count", 0)
                },
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()

            logger.warning(
                "payment_failed",
                team_id=team_id,
                invoice_id=invoice["id"]
            )

            # Send payment failure notification
            from app.services.notification_service import notification_service
            try:
                retry_date = None
                if invoice.get("next_payment_attempt"):
                    from datetime import datetime
                    retry_date = datetime.fromtimestamp(invoice["next_payment_attempt"]).strftime("%B %d, %Y")

                await notification_service.send_payment_failed_notification(
                    workspace=workspace,
                    amount=invoice["amount_due"] / 100,
                    retry_date=retry_date,
                    db=db
                )
            except Exception as notify_error:
                logger.error("payment_failed_notification_error", error=str(notify_error))

        except Exception as e:
            logger.error(
                "handle_payment_failed_error",
                error=str(e),
                invoice_id=invoice.get("id")
            )
            raise

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str
    ) -> Dict[str, Any]:
        """Verify Stripe webhook signature"""

        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.webhook_secret
            )
            return event
        except ValueError as e:
            logger.error("invalid_webhook_payload", error=str(e))
            raise
        except stripe.error.SignatureVerificationError as e:
            logger.error("invalid_webhook_signature", error=str(e))
            raise


# Global Stripe service instance
stripe_service = StripeService()
