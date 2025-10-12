"""Billing and subscription management API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.services.stripe_service import stripe_service
from app.services.workspace_service import workspace_service
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class CreateCheckoutRequest(BaseModel):
    """Request to create checkout session"""
    team_id: str
    tier: str
    success_url: str = "https://your-app.com/subscription/success"
    cancel_url: str = "https://your-app.com/subscription/cancel"


class BillingPortalRequest(BaseModel):
    """Request to access billing portal"""
    team_id: str
    return_url: str = "https://your-app.com/settings"


class UpgradeTierRequest(BaseModel):
    """Request to upgrade subscription tier"""
    team_id: str
    new_tier: str


@router.post("/billing/checkout")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create Stripe Checkout session for subscription"""

    try:
        # Validate workspace exists
        workspace = await workspace_service.get_workspace_by_team_id(request.team_id, db)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Validate tier
        valid_tiers = ["starter", "growth", "business", "enterprise"]
        if request.tier not in valid_tiers:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {valid_tiers}")

        # Create checkout session
        checkout_data = await stripe_service.create_checkout_session(
            team_id=request.team_id,
            tier=request.tier,
            db=db,
            success_url=request.success_url,
            cancel_url=request.cancel_url
        )

        logger.info(
            "checkout_session_created_via_api",
            team_id=request.team_id,
            tier=request.tier
        )

        return checkout_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "create_checkout_error",
            error=str(e),
            team_id=request.team_id
        )
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/billing/portal")
async def create_billing_portal_session(
    request: BillingPortalRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create Stripe billing portal session"""

    try:
        # Validate workspace exists
        workspace = await workspace_service.get_workspace_by_team_id(request.team_id, db)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if not workspace.stripe_customer_id:
            raise HTTPException(
                status_code=400,
                detail="No active subscription found. Please subscribe first."
            )

        # Create portal session
        portal_data = await stripe_service.create_billing_portal_session(
            team_id=request.team_id,
            db=db,
            return_url=request.return_url
        )

        logger.info(
            "billing_portal_created",
            team_id=request.team_id
        )

        return portal_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "create_billing_portal_error",
            error=str(e),
            team_id=request.team_id
        )
        raise HTTPException(status_code=500, detail="Failed to create billing portal session")


@router.get("/billing/subscription/{team_id}")
async def get_subscription_info(
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get workspace subscription information"""

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Calculate trial days remaining
        trial_days_remaining = None
        if workspace.trial_ends_at and workspace.subscription_status == "trial":
            from datetime import datetime
            days_left = (workspace.trial_ends_at - datetime.utcnow()).days
            trial_days_remaining = max(0, days_left)

        # Calculate query usage percentage
        usage_percentage = None
        queries_remaining = None

        if workspace.monthly_query_limit is not None and workspace.monthly_query_limit > 0:
            usage_percentage = round((workspace.queries_used_this_month / workspace.monthly_query_limit) * 100, 2)
            queries_remaining = workspace.monthly_query_limit - workspace.queries_used_this_month

        return {
            "team_id": team_id,
            "team_name": workspace.team_name,
            "subscription_status": workspace.subscription_status,
            "subscription_tier": workspace.subscription_tier,
            "is_active": workspace.is_subscription_active,
            "trial_ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else None,
            "trial_days_remaining": trial_days_remaining,
            "current_period_start": workspace.current_period_start.isoformat() if workspace.current_period_start else None,
            "current_period_end": workspace.current_period_end.isoformat() if workspace.current_period_end else None,
            "monthly_query_limit": workspace.monthly_query_limit,
            "queries_used_this_month": workspace.queries_used_this_month,
            "queries_remaining": queries_remaining,
            "usage_percentage": usage_percentage,
            "has_stripe_subscription": bool(workspace.stripe_subscription_id),
            "last_payment_date": workspace.last_payment_date.isoformat() if workspace.last_payment_date else None,
            "last_payment_amount": workspace.last_payment_amount
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_subscription_info_error",
            error=str(e),
            team_id=team_id
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve subscription information")


@router.get("/billing/pricing")
async def get_pricing_tiers():
    """Get available pricing tiers and their features"""

    return {
        "tiers": [
            {
                "id": "starter",
                "name": "Starter",
                "description": "Perfect for small teams getting started",
                "max_users": 50,
                "monthly_queries": 500,
                "features": [
                    "AI-powered search",
                    "Slash commands (/ask, /find)",
                    "@mention support",
                    "14-day free trial",
                    "Email support"
                ]
            },
            {
                "id": "growth",
                "name": "Growth",
                "description": "For growing teams with higher usage",
                "max_users": 250,
                "monthly_queries": 2000,
                "features": [
                    "Everything in Starter",
                    "Priority indexing",
                    "Advanced analytics",
                    "Priority email support"
                ]
            },
            {
                "id": "business",
                "name": "Business",
                "description": "For established teams with heavy usage",
                "max_users": 1000,
                "monthly_queries": 10000,
                "features": [
                    "Everything in Growth",
                    "Custom indexing rules",
                    "Dedicated support channel",
                    "SSO (coming soon)",
                    "Advanced permissions"
                ]
            },
            {
                "id": "enterprise",
                "name": "Enterprise",
                "description": "For large organizations",
                "max_users": "Unlimited",
                "monthly_queries": 50000,
                "features": [
                    "Everything in Business",
                    "Unlimited queries (fair use)",
                    "Custom integrations",
                    "Dedicated account manager",
                    "SLA guarantee",
                    "Custom contract terms"
                ]
            }
        ]
    }


@router.post("/billing/upgrade")
async def upgrade_tier(
    request: UpgradeTierRequest,
    db: AsyncSession = Depends(get_db)
):
    """Trigger tier upgrade (creates checkout session for new tier)"""

    try:
        workspace = await workspace_service.get_workspace_by_team_id(request.team_id, db)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Validate new tier
        tier_order = {"starter": 1, "growth": 2, "business": 3, "enterprise": 4}
        current_tier_level = tier_order.get(workspace.subscription_tier, 0)
        new_tier_level = tier_order.get(request.new_tier, 0)

        if new_tier_level <= current_tier_level:
            raise HTTPException(
                status_code=400,
                detail="New tier must be higher than current tier. Use billing portal to downgrade."
            )

        # If already has subscription, redirect to portal to change subscription
        if workspace.stripe_subscription_id:
            portal_data = await stripe_service.create_billing_portal_session(
                team_id=request.team_id,
                db=db,
                return_url="https://your-app.com/settings"
            )
            return {
                "message": "Use billing portal to upgrade",
                "portal_url": portal_data["portal_url"]
            }

        # If no subscription yet (trial), create checkout for new tier
        checkout_data = await stripe_service.create_checkout_session(
            team_id=request.team_id,
            tier=request.new_tier,
            db=db,
            success_url="https://your-app.com/subscription/success",
            cancel_url="https://your-app.com/subscription/cancel"
        )

        return {
            "message": "Checkout session created",
            "checkout_url": checkout_data["checkout_url"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "upgrade_tier_error",
            error=str(e),
            team_id=request.team_id
        )
        raise HTTPException(status_code=500, detail="Failed to process upgrade")
