"""
Integration registry for managing all external source adapters

This module provides centralized registration and discovery of integration adapters.
"""
from typing import Dict, Type, Optional, List
from app.core.logging import get_logger
from app.integrations.base_adapter import BaseSourceAdapter

logger = get_logger(__name__)


class IntegrationRegistry:
    """
    Central registry for all integration adapters

    Usage:
        # Register an adapter
        IntegrationRegistry.register("github", GitHubAdapter)

        # Get an adapter instance
        adapter = IntegrationRegistry.get_adapter("github", team_id, credentials)

        # List available integrations
        sources = IntegrationRegistry.list_available()
    """

    _adapters: Dict[str, Type[BaseSourceAdapter]] = {}

    @classmethod
    def register(cls, source_name: str, adapter_class: Type[BaseSourceAdapter]):
        """
        Register an integration adapter

        Args:
            source_name: Lowercase source identifier (e.g., "github", "jira")
            adapter_class: Adapter class (must inherit from BaseSourceAdapter)
        """
        if not issubclass(adapter_class, BaseSourceAdapter):
            raise ValueError(
                f"Adapter {adapter_class.__name__} must inherit from BaseSourceAdapter"
            )

        source_name = source_name.lower()

        if source_name in cls._adapters:
            logger.warning(
                "adapter_already_registered",
                source=source_name,
                old_class=cls._adapters[source_name].__name__,
                new_class=adapter_class.__name__
            )

        cls._adapters[source_name] = adapter_class
        logger.info(
            "adapter_registered",
            source=source_name,
            adapter_class=adapter_class.__name__
        )

    @classmethod
    def get_adapter(
        cls,
        source_name: str,
        team_id: str,
        credentials: Dict
    ) -> Optional[BaseSourceAdapter]:
        """
        Get an instance of a registered adapter

        Args:
            source_name: Source to get adapter for
            team_id: Workspace team ID
            credentials: Authentication credentials

        Returns:
            Adapter instance or None if not registered
        """
        source_name = source_name.lower()

        adapter_class = cls._adapters.get(source_name)
        if not adapter_class:
            logger.error("adapter_not_found", source=source_name)
            return None

        try:
            adapter = adapter_class(team_id=team_id, credentials=credentials)
            logger.info(
                "adapter_instantiated",
                source=source_name,
                team_id=team_id
            )
            return adapter
        except Exception as e:
            logger.error(
                "adapter_instantiation_failed",
                source=source_name,
                error=str(e)
            )
            return None

    @classmethod
    def is_registered(cls, source_name: str) -> bool:
        """Check if a source adapter is registered"""
        return source_name.lower() in cls._adapters

    @classmethod
    def list_available(cls) -> List[str]:
        """Get list of all registered source names"""
        return list(cls._adapters.keys())

    @classmethod
    def get_capabilities(cls, source_name: str) -> Optional[Dict]:
        """
        Get capabilities of a registered adapter

        Args:
            source_name: Source to get capabilities for

        Returns:
            Dict of capabilities or None if not registered
        """
        source_name = source_name.lower()
        adapter_class = cls._adapters.get(source_name)

        if not adapter_class:
            return None

        # Create temporary instance to get capabilities
        # Use dummy credentials since we only need capabilities
        try:
            temp_adapter = adapter_class(
                team_id="__temp__",
                credentials={}
            )
            capabilities = temp_adapter.get_capabilities()

            return {
                'supports_search': capabilities.supports_search,
                'supports_incremental_sync': capabilities.supports_incremental_sync,
                'supports_webhooks': capabilities.supports_webhooks,
                'supports_full_text': capabilities.supports_full_text,
                'supports_code': capabilities.supports_code,
                'supports_files': capabilities.supports_files,
                'supports_permissions': capabilities.supports_permissions,
                'supports_versioning': capabilities.supports_versioning,
                'supports_comments': capabilities.supports_comments,
                'can_detect_links': capabilities.can_detect_links,
            }
        except Exception as e:
            logger.error(
                "capabilities_retrieval_failed",
                source=source_name,
                error=str(e)
            )
            return None

    @classmethod
    def unregister(cls, source_name: str):
        """
        Unregister an adapter (mainly for testing)

        Args:
            source_name: Source to unregister
        """
        source_name = source_name.lower()
        if source_name in cls._adapters:
            del cls._adapters[source_name]
            logger.info("adapter_unregistered", source=source_name)

    @classmethod
    def clear(cls):
        """Clear all registered adapters (mainly for testing)"""
        cls._adapters.clear()
        logger.info("all_adapters_cleared")
