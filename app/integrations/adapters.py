"""
Integration adapter registration

This module registers all available integration adapters with the registry.
Import this module during application startup to ensure adapters are registered.
"""
from app.integrations.registry import IntegrationRegistry
from app.integrations.github_adapter import GitHubAdapter
from app.integrations.slack_adapter import SlackAdapter
from app.core.logging import get_logger

logger = get_logger(__name__)


def register_all_adapters():
    """
    Register all available integration adapters

    Call this during application startup to make adapters available.
    """
    # Register Slack
    IntegrationRegistry.register("slack", SlackAdapter)

    # Register GitHub
    IntegrationRegistry.register("github", GitHubAdapter)

    logger.info(
        "all_adapters_registered",
        count=len(IntegrationRegistry.list_available()),
        sources=IntegrationRegistry.list_available()
    )


# Auto-register when this module is imported
register_all_adapters()
