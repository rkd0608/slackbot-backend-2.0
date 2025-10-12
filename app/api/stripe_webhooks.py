"""Stripe webhook endpoints"""
from fastapi import APIRouter, Request, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.stripe_service import stripe_service
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/stripe/webhooks")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db)
):
    """Handle Stripe webhook events"""

    try:
        # Get raw request body
        payload = await request.body()

        # Verify webhook signature
        try:
            event = stripe_service.verify_webhook_signature(payload, stripe_signature)
        except Exception as e:
            logger.error("webhook_signature_verification_failed", error=str(e))
            raise HTTPException(status_code=400, detail="Invalid signature")

        event_type = event["type"]
        data = event["data"]["object"]

        logger.info(
            "stripe_webhook_received",
            event_type=event_type,
            event_id=event["id"]
        )

        # Handle different event types
        if event_type == "customer.subscription.created":
            await stripe_service.handle_subscription_created(data, db)

        elif event_type == "customer.subscription.updated":
            await stripe_service.handle_subscription_updated(data, db)

        elif event_type == "customer.subscription.deleted":
            await stripe_service.handle_subscription_deleted(data, db)

        elif event_type == "invoice.payment_succeeded":
            await stripe_service.handle_payment_succeeded(data, db)

        elif event_type == "invoice.payment_failed":
            await stripe_service.handle_payment_failed(data, db)

        else:
            logger.info("unhandled_webhook_event", event_type=event_type)

        return {"status": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "stripe_webhook_error",
            error=str(e),
            event_type=event.get("type") if event else None
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed")
