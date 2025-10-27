"""
Integration Configuration Service

Manages which integrations are enabled per team and stores configuration settings.
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.logging import get_logger
from app.models.integration import Integration, UserIntegrationConnection
from app.integrations.registry import IntegrationRegistry

logger = get_logger(__name__)


class IntegrationConfigService:
    """
    Service for managing integration configurations

    Handles:
    - Enabling/disabling integrations per team
    - Storing integration settings
    - Managing user connections to integrations
    - Checking which integrations are available
    """

    async def get_enabled_integrations(
        self,
        team_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Get all enabled integrations for a team

        Args:
            team_id: Workspace team ID
            db: Database session

        Returns:
            List of enabled integrations with their configurations
        """
        try:
            result = await db.execute(
                select(Integration).where(
                    and_(
                        Integration.team_id == team_id,
                        Integration.is_active == "true"
                    )
                )
            )

            integrations = result.scalars().all()

            return [
                {
                    'id': integration.id,
                    'type': integration.integration_type,
                    'is_active': integration.is_active == "true",
                    'settings': integration.settings,
                    'connected_at': integration.connected_at.isoformat() if integration.connected_at else None,
                    'capabilities': IntegrationRegistry.get_capabilities(integration.integration_type)
                }
                for integration in integrations
            ]

        except Exception as e:
            logger.error(
                "get_enabled_integrations_failed",
                team_id=team_id,
                error=str(e)
            )
            return []

    async def is_integration_enabled(
        self,
        team_id: str,
        integration_type: str,
        db: AsyncSession
    ) -> bool:
        """
        Check if an integration is enabled for a team

        Args:
            team_id: Workspace team ID
            integration_type: Integration type (e.g., "github", "jira")
            db: Database session

        Returns:
            True if enabled, False otherwise
        """
        try:
            result = await db.execute(
                select(Integration).where(
                    and_(
                        Integration.team_id == team_id,
                        Integration.integration_type == integration_type,
                        Integration.is_active == "true"
                    )
                )
            )

            integration = result.scalar_one_or_none()
            return integration is not None

        except Exception as e:
            logger.error(
                "is_integration_enabled_check_failed",
                team_id=team_id,
                integration_type=integration_type,
                error=str(e)
            )
            return False

    async def get_user_connections(
        self,
        user_id: str,
        team_id: str,
        db: AsyncSession,
        integration_type: Optional[str] = None
    ) -> List[UserIntegrationConnection]:
        """
        Get user's connections to integrations

        Args:
            user_id: User ID
            team_id: Team ID
            db: Database session
            integration_type: Optional filter by integration type

        Returns:
            List of user connections
        """
        try:
            conditions = [
                UserIntegrationConnection.user_id == user_id,
                UserIntegrationConnection.team_id == team_id,
                UserIntegrationConnection.is_active == True
            ]

            if integration_type:
                conditions.append(
                    UserIntegrationConnection.integration_type == integration_type
                )

            result = await db.execute(
                select(UserIntegrationConnection).where(and_(*conditions))
            )

            return list(result.scalars().all())

        except Exception as e:
            logger.error(
                "get_user_connections_failed",
                user_id=user_id,
                team_id=team_id,
                error=str(e)
            )
            return []

    async def get_available_integrations(self) -> List[Dict[str, Any]]:
        """
        Get all integrations that can be enabled

        Returns:
            List of available integrations with their capabilities
        """
        available_sources = IntegrationRegistry.list_available()

        return [
            {
                'type': source,
                'name': source.title(),
                'capabilities': IntegrationRegistry.get_capabilities(source),
                'is_registered': True
            }
            for source in available_sources
        ]

    async def get_integration_stats(
        self,
        team_id: str,
        integration_type: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Get statistics for an integration

        Args:
            team_id: Team ID
            integration_type: Integration type
            db: Database session

        Returns:
            Dict with integration statistics
        """
        try:
            from app.models.cross_source_node import CrossSourceNode
            from app.models.cross_source_edge import CrossSourceEdge
            from sqlalchemy import func

            # Count nodes from this source
            node_count_result = await db.execute(
                select(func.count(CrossSourceNode.canonical_id)).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == integration_type,
                        CrossSourceNode.is_deleted == "false"
                    )
                )
            )
            node_count = node_count_result.scalar()

            # Count node types
            node_types_result = await db.execute(
                select(
                    CrossSourceNode.node_type,
                    func.count(CrossSourceNode.canonical_id)
                ).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == integration_type,
                        CrossSourceNode.is_deleted == "false"
                    )
                ).group_by(CrossSourceNode.node_type)
            )
            node_types = {row[0].value: row[1] for row in node_types_result.all()}

            # Count edges
            edge_count_result = await db.execute(
                select(func.count(CrossSourceEdge.id)).where(
                    and_(
                        CrossSourceEdge.team_id == team_id,
                        or_(
                            CrossSourceEdge.source_node_id.like(f"{integration_type}:%"),
                            CrossSourceEdge.target_node_id.like(f"{integration_type}:%")
                        ),
                        CrossSourceEdge.is_deleted == "false"
                    )
                )
            )
            edge_count = edge_count_result.scalar()

            # Get latest indexed node
            latest_result = await db.execute(
                select(CrossSourceNode.indexed_at).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == integration_type
                    )
                ).order_by(CrossSourceNode.indexed_at.desc()).limit(1)
            )
            latest_indexed = latest_result.scalar_one_or_none()

            return {
                'integration_type': integration_type,
                'total_nodes': node_count or 0,
                'node_types': node_types,
                'total_edges': edge_count or 0,
                'last_indexed': latest_indexed.isoformat() if latest_indexed else None
            }

        except Exception as e:
            logger.error(
                "get_integration_stats_failed",
                team_id=team_id,
                integration_type=integration_type,
                error=str(e)
            )
            return {
                'integration_type': integration_type,
                'total_nodes': 0,
                'node_types': {},
                'total_edges': 0,
                'last_indexed': None,
                'error': str(e)
            }

    async def update_integration_settings(
        self,
        team_id: str,
        integration_type: str,
        settings: Dict[str, Any],
        db: AsyncSession
    ) -> bool:
        """
        Update settings for an integration

        Args:
            team_id: Team ID
            integration_type: Integration type
            settings: New settings dict
            db: Database session

        Returns:
            True if successful, False otherwise
        """
        try:
            result = await db.execute(
                select(Integration).where(
                    and_(
                        Integration.team_id == team_id,
                        Integration.integration_type == integration_type
                    )
                )
            )

            integration = result.scalar_one_or_none()

            if not integration:
                logger.warning(
                    "integration_not_found_for_update",
                    team_id=team_id,
                    integration_type=integration_type
                )
                return False

            # Update settings
            integration.settings = {**(integration.settings or {}), **settings}
            await db.commit()

            logger.info(
                "integration_settings_updated",
                team_id=team_id,
                integration_type=integration_type
            )

            return True

        except Exception as e:
            logger.error(
                "update_integration_settings_failed",
                team_id=team_id,
                integration_type=integration_type,
                error=str(e)
            )
            await db.rollback()
            return False


def get_integration_config_service() -> IntegrationConfigService:
    """Get integration config service instance"""
    return IntegrationConfigService()
