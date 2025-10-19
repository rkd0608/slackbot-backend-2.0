"""Analytics service for dashboard metrics and insights"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, case
from app.models.query_log import QueryLog
from app.models.channel import Channel
from app.models.message import Message
from app.models.user import User
from app.core.logging import get_logger

logger = get_logger(__name__)


class AnalyticsService:
    """Service for calculating dashboard analytics metrics"""

    async def get_queries_this_week(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Tuple[int, float, str]:
        """
        Get query count for current week with percentage change from last week

        Returns:
            (count, percentage_change, trend)
            Example: (150, 25.5, 'up') means 150 queries, 25.5% increase
        """
        try:
            now = datetime.utcnow()
            # Current week: last 7 days
            week_start = now - timedelta(days=7)

            # Last week: 7-14 days ago
            last_week_start = now - timedelta(days=14)
            last_week_end = week_start

            # Count this week's queries
            this_week_stmt = select(func.count(QueryLog.id)).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            )
            this_week_result = await db.execute(this_week_stmt)
            this_week_count = this_week_result.scalar() or 0

            # Count last week's queries
            last_week_stmt = select(func.count(QueryLog.id)).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= last_week_start,
                    QueryLog.created_at < last_week_end
                )
            )
            last_week_result = await db.execute(last_week_stmt)
            last_week_count = last_week_result.scalar() or 0

            # Calculate percentage change
            if last_week_count > 0:
                percentage_change = ((this_week_count - last_week_count) / last_week_count) * 100
            else:
                percentage_change = 100.0 if this_week_count > 0 else 0.0

            trend = 'up' if percentage_change > 0 else 'down' if percentage_change < 0 else 'neutral'

            return this_week_count, round(percentage_change, 1), trend

        except Exception as e:
            logger.error("get_queries_this_week_error", error=str(e), team_id=team_id)
            return 0, 0.0, 'neutral'

    async def get_accuracy_feedback(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Tuple[float, float, str, int]:
        """
        Get accuracy based on thumbs up/down feedback

        Returns:
            (accuracy_percentage, percentage_change, trend, total_ratings)
            Accuracy = (thumbs_up / total_ratings) * 100
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)
            last_week_start = now - timedelta(days=14)
            last_week_end = week_start

            # This week's accuracy
            this_week_stmt = select(
                func.count(case((QueryLog.user_rating == 5, 1))).label('thumbs_up'),
                func.count(QueryLog.id).label('total')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.user_rating.isnot(None),
                    QueryLog.created_at >= week_start
                )
            )
            this_week_result = await db.execute(this_week_stmt)
            this_week_row = this_week_result.one()
            this_week_thumbs_up = this_week_row.thumbs_up or 0
            this_week_total = this_week_row.total or 0

            # Last week's accuracy
            last_week_stmt = select(
                func.count(case((QueryLog.user_rating == 5, 1))).label('thumbs_up'),
                func.count(QueryLog.id).label('total')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.user_rating.isnot(None),
                    QueryLog.created_at >= last_week_start,
                    QueryLog.created_at < last_week_end
                )
            )
            last_week_result = await db.execute(last_week_stmt)
            last_week_row = last_week_result.one()
            last_week_thumbs_up = last_week_row.thumbs_up or 0
            last_week_total = last_week_row.total or 0

            # Calculate accuracies
            this_week_accuracy = (this_week_thumbs_up / this_week_total * 100) if this_week_total > 0 else 0.0
            last_week_accuracy = (last_week_thumbs_up / last_week_total * 100) if last_week_total > 0 else 0.0

            # Calculate percentage change
            if last_week_accuracy > 0:
                percentage_change = this_week_accuracy - last_week_accuracy
            else:
                percentage_change = 0.0

            trend = 'up' if percentage_change > 0 else 'down' if percentage_change < 0 else 'neutral'

            return round(this_week_accuracy, 1), round(percentage_change, 1), trend, this_week_total

        except Exception as e:
            logger.error("get_accuracy_feedback_error", error=str(e), team_id=team_id)
            return 0.0, 0.0, 'neutral', 0

    async def get_monitored_channels_count(
        self,
        team_id: str,
        db: AsyncSession
    ) -> int:
        """Get count of channels being indexed"""
        try:
            stmt = select(func.count(Channel.id)).where(
                and_(
                    Channel.team_id == team_id,
                    Channel.indexing_enabled == 1,
                    Channel.is_archived == 0
                )
            )
            result = await db.execute(stmt)
            return result.scalar() or 0

        except Exception as e:
            logger.error("get_monitored_channels_count_error", error=str(e), team_id=team_id)
            return 0

    async def get_queries_over_time(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, List]:
        """
        Get daily query counts for this week and last week

        Returns:
            {
                "labels": ["Mon", "Tue", "Wed", ...],
                "this_week": [10, 15, 20, ...],
                "last_week": [8, 12, 18, ...]
            }
        """
        try:
            now = datetime.utcnow()

            # Get data for last 14 days
            two_weeks_ago = now - timedelta(days=14)

            stmt = select(
                func.date(QueryLog.created_at).label('query_date'),
                func.count(QueryLog.id).label('count')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= two_weeks_ago
                )
            ).group_by(
                func.date(QueryLog.created_at)
            ).order_by(
                func.date(QueryLog.created_at)
            )

            result = await db.execute(stmt)
            rows = result.fetchall()

            # Build dict of date -> count
            date_counts = {row.query_date: row.count for row in rows}

            # Generate arrays for last 7 days (this week) and 7-14 days ago (last week)
            labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            this_week_data = []
            last_week_data = []

            for i in range(7):
                # This week (0-6 days ago)
                this_week_date = (now - timedelta(days=6-i)).date()
                this_week_data.append(date_counts.get(this_week_date, 0))

                # Last week (7-13 days ago)
                last_week_date = (now - timedelta(days=13-i)).date()
                last_week_data.append(date_counts.get(last_week_date, 0))

            return {
                "labels": labels,
                "this_week": this_week_data,
                "last_week": last_week_data
            }

        except Exception as e:
            logger.error("get_queries_over_time_error", error=str(e), team_id=team_id)
            return {
                "labels": ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                "this_week": [0] * 7,
                "last_week": [0] * 7
            }

    async def get_top_searched_topics(
        self,
        team_id: str,
        limit: int,
        db: AsyncSession
    ) -> List[Dict[str, any]]:
        """
        Get top searched topics from query text

        For now, extracts simple keywords. Can be enhanced with NLP later.
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)

            # Get recent queries
            stmt = select(QueryLog.query_text).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            ).limit(1000)  # Analyze last 1000 queries

            result = await db.execute(stmt)
            queries = [row[0] for row in result.fetchall()]

            # Simple keyword extraction (can be improved with spaCy/NLTK)
            from collections import Counter
            import re

            # Extract words (simple tokenization)
            all_words = []
            stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                         'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
                         'what', 'when', 'where', 'who', 'how', 'why', 'can', 'did', 'does',
                         'do', 'i', 'we', 'you', 'they', 'he', 'she', 'it'}

            for query in queries:
                words = re.findall(r'\b[a-z]{3,}\b', query.lower())
                all_words.extend([w for w in words if w not in stop_words])

            # Count frequencies
            word_counts = Counter(all_words)
            top_topics = word_counts.most_common(limit)

            return [
                {"topic": topic, "count": count}
                for topic, count in top_topics
            ]

        except Exception as e:
            logger.error("get_top_searched_topics_error", error=str(e), team_id=team_id)
            return []

    async def get_active_users_count(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Tuple[int, float, str]:
        """
        Get count of active users (users who made queries) with week-over-week change

        Returns:
            (count, percentage_change, trend)
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)
            last_week_start = now - timedelta(days=14)
            last_week_end = week_start

            # Count unique users this week
            this_week_stmt = select(func.count(func.distinct(QueryLog.user_id))).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            )
            this_week_result = await db.execute(this_week_stmt)
            this_week_count = this_week_result.scalar() or 0

            # Count unique users last week
            last_week_stmt = select(func.count(func.distinct(QueryLog.user_id))).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= last_week_start,
                    QueryLog.created_at < last_week_end
                )
            )
            last_week_result = await db.execute(last_week_stmt)
            last_week_count = last_week_result.scalar() or 0

            # Calculate percentage change
            if last_week_count > 0:
                percentage_change = ((this_week_count - last_week_count) / last_week_count) * 100
            else:
                percentage_change = 100.0 if this_week_count > 0 else 0.0

            trend = 'up' if percentage_change > 0 else 'down' if percentage_change < 0 else 'neutral'

            return this_week_count, round(percentage_change, 1), trend

        except Exception as e:
            logger.error("get_active_users_count_error", error=str(e), team_id=team_id)
            return 0, 0.0, 'neutral'

    async def get_time_saved_this_week(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Tuple[float, float, str]:
        """
        Calculate time saved based on queries answered
        Assumption: Each query saves ~5 minutes of search time

        Returns:
            (hours_saved, percentage_change, trend)
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)
            last_week_start = now - timedelta(days=14)
            last_week_end = week_start

            MINUTES_SAVED_PER_QUERY = 5  # Configurable assumption

            # This week's queries
            this_week_stmt = select(func.count(QueryLog.id)).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            )
            this_week_result = await db.execute(this_week_stmt)
            this_week_count = this_week_result.scalar() or 0

            # Last week's queries
            last_week_stmt = select(func.count(QueryLog.id)).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= last_week_start,
                    QueryLog.created_at < last_week_end
                )
            )
            last_week_result = await db.execute(last_week_stmt)
            last_week_count = last_week_result.scalar() or 0

            # Calculate hours saved
            this_week_hours = (this_week_count * MINUTES_SAVED_PER_QUERY) / 60
            last_week_hours = (last_week_count * MINUTES_SAVED_PER_QUERY) / 60

            # Calculate percentage change
            if last_week_hours > 0:
                percentage_change = ((this_week_hours - last_week_hours) / last_week_hours) * 100
            else:
                percentage_change = 100.0 if this_week_hours > 0 else 0.0

            trend = 'up' if percentage_change > 0 else 'down' if percentage_change < 0 else 'neutral'

            return round(this_week_hours, 1), round(percentage_change, 1), trend

        except Exception as e:
            logger.error("get_time_saved_this_week_error", error=str(e), team_id=team_id)
            return 0.0, 0.0, 'neutral'

    async def get_queries_single_week(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, List]:
        """
        Get daily query counts for current week only (no comparison)

        Returns:
            {
                "labels": ["Mon", "Tue", "Wed", ...],
                "data": [10, 15, 20, ...]
            }
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)

            stmt = select(
                func.date(QueryLog.created_at).label('query_date'),
                func.count(QueryLog.id).label('count')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            ).group_by(
                func.date(QueryLog.created_at)
            ).order_by(
                func.date(QueryLog.created_at)
            )

            result = await db.execute(stmt)
            rows = result.fetchall()

            # Build dict of date -> count
            date_counts = {row.query_date: row.count for row in rows}

            # Generate arrays for last 7 days
            labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            data = []

            for i in range(7):
                date = (now - timedelta(days=6-i)).date()
                data.append(date_counts.get(date, 0))

            return {
                "labels": labels,
                "data": data
            }

        except Exception as e:
            logger.error("get_queries_single_week_error", error=str(e), team_id=team_id)
            return {
                "labels": ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                "data": [0] * 7
            }

    async def get_feedback_distribution(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, int]:
        """
        Get feedback distribution for pie chart

        Returns:
            {
                "positive": 45,
                "negative": 10,
                "no_response": 95
            }
        """
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)

            # Get feedback counts
            stmt = select(
                func.count(case((QueryLog.user_rating == 5, 1))).label('positive'),
                func.count(case((QueryLog.user_rating == 1, 1))).label('negative'),
                func.count(case((QueryLog.user_rating.is_(None), 1))).label('no_response')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            )

            result = await db.execute(stmt)
            row = result.one()

            return {
                "positive": row.positive or 0,
                "negative": row.negative or 0,
                "no_response": row.no_response or 0
            }

        except Exception as e:
            logger.error("get_feedback_distribution_error", error=str(e), team_id=team_id)
            return {"positive": 0, "negative": 0, "no_response": 0}

    async def get_top_active_users(
        self,
        team_id: str,
        limit: int,
        db: AsyncSession
    ) -> List[Dict[str, any]]:
        """Get top N active users based on query count"""
        try:
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)

            stmt = select(
                QueryLog.user_id,
                func.count(QueryLog.id).label('query_count'),
                func.count(case((QueryLog.user_rating == 5, 1))).label('positive_ratings'),
                func.count(case((QueryLog.user_rating.isnot(None), 1))).label('total_ratings')
            ).where(
                and_(
                    QueryLog.team_id == team_id,
                    QueryLog.created_at >= week_start
                )
            ).group_by(
                QueryLog.user_id
            ).order_by(
                desc('query_count')
            ).limit(limit)

            result = await db.execute(stmt)
            rows = result.fetchall()

            users = []
            for row in rows:
                # Get user info
                user_stmt = select(User).where(User.user_id == row.user_id)
                user_result = await db.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                # Calculate accuracy percentage
                accuracy = (row.positive_ratings / row.total_ratings * 100) if row.total_ratings > 0 else None

                users.append({
                    "user_id": row.user_id,
                    "display_name": user.display_name if user else row.user_id,
                    "query_count": row.query_count,
                    "accuracy_percentage": round(accuracy, 1) if accuracy is not None else None
                })

            return users

        except Exception as e:
            logger.error("get_top_active_users_error", error=str(e), team_id=team_id)
            return []

    async def get_most_active_user(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, any]]:
        """Get most active user based on query count (for backward compatibility)"""
        users = await self.get_top_active_users(team_id, 1, db)
        return users[0] if users else None

    async def get_most_indexed_channel(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, any]]:
        """Get channel with most indexed messages"""
        try:
            stmt = select(
                Channel.channel_id,
                Channel.channel_name,
                func.count(Message.id).label('message_count')
            ).join(
                Message,
                and_(
                    Message.channel_id == Channel.channel_id,
                    Message.team_id == Channel.team_id
                )
            ).where(
                and_(
                    Channel.team_id == team_id,
                    Channel.indexing_enabled == 1,
                    Channel.is_archived == 0
                )
            ).group_by(
                Channel.channel_id,
                Channel.channel_name
            ).order_by(
                desc('message_count')
            ).limit(1)

            result = await db.execute(stmt)
            row = result.one_or_none()

            if not row:
                return None

            return {
                "channel_id": row.channel_id,
                "channel_name": row.channel_name,
                "message_count": row.message_count
            }

        except Exception as e:
            logger.error("get_most_indexed_channel_error", error=str(e), team_id=team_id)
            return None


# Global instance
analytics_service = AnalyticsService()
