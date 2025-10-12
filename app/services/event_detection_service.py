"""Event detection service for identifying significant organizational events"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from openai import AsyncOpenAI
import json
import re
import uuid

from app.models.user_expertise import SignificantEvent
from app.models.message import Message
from app.models.entity import Entity
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EventDetectionService:
    """Detect and track significant organizational events"""

    # Event types
    EVENT_DEPLOYMENT = "deployment"
    EVENT_MIGRATION = "migration"
    EVENT_INCIDENT = "incident"
    EVENT_OUTAGE = "outage"
    EVENT_FEATURE_LAUNCH = "feature_launch"
    EVENT_MEETING = "meeting"
    EVENT_ANNOUNCEMENT = "announcement"

    # Event detection patterns
    DEPLOYMENT_PATTERNS = [
        r'\b(deploy|deployed|deploying|deployment)\b',
        r'\b(release|released|releasing)\b',
        r'\b(shipped|shipping)\b',
        r'\b(push|pushed|pushing)\s+(to\s+)?(prod|production)\b'
    ]

    MIGRATION_PATTERNS = [
        r'\b(migrat(e|ed|ing|ion))\b',
        r'\b(upgrade|upgraded|upgrading)\b',
        r'\b(switch|switched|switching)\s+(to|from)\b',
        r'\b(move|moved|moving)\s+(to|from)\b.*\b(database|db|server|cloud)\b'
    ]

    INCIDENT_PATTERNS = [
        r'\b(incident|outage|down|failing|broken)\b',
        r'\b(emergency|urgent|critical)\b',
        r'\b(page|paged|paging)\b.*\b(oncall|on-call)\b',
        r'\b(sev[0-9]|p[0-9])\b',  # Severity levels
        r'\b(production\s+issue|prod\s+issue)\b'
    ]

    FEATURE_LAUNCH_PATTERNS = [
        r'\b(launch|launched|launching)\b',
        r'\b(new\s+feature|feature\s+release)\b',
        r'\b(beta|alpha)\s+(test|testing|release)\b',
        r'\b(go\s+live|went\s+live|going\s+live)\b'
    ]

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.use_llm = getattr(settings, 'enable_llm_event_detection', True)

    async def detect_event_from_message(
        self,
        message: Message,
        db: AsyncSession
    ) -> Optional[SignificantEvent]:
        """Detect if a message indicates a significant event"""

        try:
            text = message.text_processed or message.text

            if not text or len(text) < 20:
                return None

            # Use LLM for detection if enabled
            if self.use_llm:
                event_data = await self._detect_event_llm(text, message)
            else:
                event_data = self._detect_event_regex(text)

            if not event_data:
                return None

            # Check if event already exists
            event_id = self._generate_event_id(event_data, message)

            result = await db.execute(
                select(SignificantEvent).where(SignificantEvent.event_id == event_id)
            )
            existing_event = result.scalar_one_or_none()

            if existing_event:
                # Update existing event
                await self._update_event(existing_event, message, db)
                return existing_event

            # Create new event
            event = SignificantEvent(
                event_id=event_id,
                event_type=event_data['type'],
                event_name=event_data['name'],
                event_description=event_data.get('description'),
                start_time=message.timestamp,
                detected_at=datetime.utcnow(),
                channels_discussed=[message.channel_id],
                message_ids=[message.message_id],
                participants=[message.user_id],
                message_count=1,
                severity=event_data.get('severity')
            )

            db.add(event)
            await db.commit()

            logger.info(
                "event_detected",
                event_id=event_id,
                event_type=event_data['type'],
                event_name=event_data['name']
            )

            return event

        except Exception as e:
            logger.error("event_detection_error", error=str(e), message_id=message.message_id)
            return None

    async def _detect_event_llm(
        self,
        text: str,
        message: Message
    ) -> Optional[Dict[str, Any]]:
        """Use LLM to detect events from text"""

        try:
            prompt = f"""Analyze this Slack message and determine if it describes a significant organizational event.

Message: "{text}"
Channel: {message.channel_name}
Timestamp: {message.timestamp}

Event types to detect:
- deployment: Software deployments to production
- migration: Database/infrastructure migrations
- incident: Production incidents or outages
- feature_launch: New feature releases
- announcement: Important organizational announcements

Return JSON:
{{
  "is_event": true/false,
  "type": "event_type",
  "name": "brief event name (max 100 chars)",
  "description": "brief description",
  "severity": "low/medium/high/critical" (for incidents only),
  "confidence": 0.0-1.0
}}

Only detect significant events. Return {{"is_event": false}} for routine messages."""

            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert at identifying significant organizational events from technical discussions."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=300
            )

            content = response.choices[0].message.content.strip()

            # Parse JSON response
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*$', '', content)

            result = json.loads(content)

            if result.get('is_event') and result.get('confidence', 0) > 0.6:
                return {
                    'type': result['type'],
                    'name': result['name'],
                    'description': result.get('description'),
                    'severity': result.get('severity')
                }

            return None

        except Exception as e:
            logger.error("llm_event_detection_error", error=str(e))
            return None

    def _detect_event_regex(self, text: str) -> Optional[Dict[str, Any]]:
        """Use regex patterns to detect events (fallback)"""

        text_lower = text.lower()

        # Check deployment
        for pattern in self.DEPLOYMENT_PATTERNS:
            if re.search(pattern, text_lower):
                return {
                    'type': self.EVENT_DEPLOYMENT,
                    'name': 'Deployment detected',
                    'description': text[:200]
                }

        # Check migration
        for pattern in self.MIGRATION_PATTERNS:
            if re.search(pattern, text_lower):
                return {
                    'type': self.EVENT_MIGRATION,
                    'name': 'Migration detected',
                    'description': text[:200]
                }

        # Check incident
        for pattern in self.INCIDENT_PATTERNS:
            if re.search(pattern, text_lower):
                # Determine severity
                severity = 'medium'
                if re.search(r'\b(critical|sev1|p1|emergency)\b', text_lower):
                    severity = 'critical'
                elif re.search(r'\b(sev2|p2|urgent)\b', text_lower):
                    severity = 'high'

                return {
                    'type': self.EVENT_INCIDENT,
                    'name': 'Incident detected',
                    'description': text[:200],
                    'severity': severity
                }

        # Check feature launch
        for pattern in self.FEATURE_LAUNCH_PATTERNS:
            if re.search(pattern, text_lower):
                return {
                    'type': self.EVENT_FEATURE_LAUNCH,
                    'name': 'Feature launch detected',
                    'description': text[:200]
                }

        return None

    def _generate_event_id(self, event_data: Dict[str, Any], message: Message) -> str:
        """Generate unique event ID"""

        # Use combination of type, date, and name for clustering related messages
        date_key = message.timestamp.strftime("%Y%m%d")
        name_key = re.sub(r'[^\w]+', '_', event_data['name'].lower())[:50]

        return f"{event_data['type']}_{date_key}_{name_key}"

    async def _update_event(
        self,
        event: SignificantEvent,
        message: Message,
        db: AsyncSession
    ):
        """Update existing event with new message"""

        # Add message
        if event.message_ids is None:
            event.message_ids = []
        if message.message_id not in event.message_ids:
            event.message_ids.append(message.message_id)
            event.message_count = len(event.message_ids)

        # Add channel
        if event.channels_discussed is None:
            event.channels_discussed = []
        if message.channel_id not in event.channels_discussed:
            event.channels_discussed.append(message.channel_id)

        # Add participant
        if event.participants is None:
            event.participants = []
        if message.user_id not in event.participants:
            event.participants.append(message.user_id)

        # Update end time
        if not event.end_time or message.timestamp > event.end_time:
            event.end_time = message.timestamp

        await db.commit()

    async def get_events_timeline(
        self,
        db: AsyncSession,
        days_back: int = 30,
        event_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get timeline of events"""

        since_date = datetime.utcnow() - timedelta(days=days_back)

        filters = [SignificantEvent.start_time >= since_date]
        if event_type:
            filters.append(SignificantEvent.event_type == event_type)

        result = await db.execute(
            select(SignificantEvent)
            .where(and_(*filters))
            .order_by(desc(SignificantEvent.start_time))
        )

        events = result.scalars().all()

        return [
            {
                'event_id': event.event_id,
                'type': event.event_type,
                'name': event.event_name,
                'description': event.event_description,
                'start_time': event.start_time.isoformat(),
                'end_time': event.end_time.isoformat() if event.end_time else None,
                'duration_hours': ((event.end_time - event.start_time).total_seconds() / 3600) if event.end_time else None,
                'message_count': event.message_count,
                'channels': event.channels_discussed,
                'participants': event.participants,
                'severity': event.severity
            }
            for event in events
        ]

    async def get_event_impact_analysis(
        self,
        event_id: str,
        entity_text: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Analyze impact of event on specific entity"""

        # Get event
        result = await db.execute(
            select(SignificantEvent).where(SignificantEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()

        if not event:
            return {'found': False}

        from app.services.entity_service import entity_service

        entity = await entity_service.get_entity_by_text(entity_text, db)

        if not entity:
            return {'found': False, 'event_found': True}

        # Get messages before event
        before_start = event.start_time - timedelta(days=7)
        before_result = await db.execute(
            select(func.count(Message.id))
            .where(
                and_(
                    Message.text.ilike(f"%{entity.canonical_form}%"),
                    Message.timestamp >= before_start,
                    Message.timestamp < event.start_time
                )
            )
        )
        before_count = before_result.scalar()

        # Get messages after event
        after_end = event.end_time or event.start_time
        after_result = await db.execute(
            select(func.count(Message.id))
            .where(
                and_(
                    Message.text.ilike(f"%{entity.canonical_form}%"),
                    Message.timestamp >= after_end,
                    Message.timestamp < after_end + timedelta(days=7)
                )
            )
        )
        after_count = after_result.scalar()

        # Calculate change
        if before_count > 0:
            percent_change = ((after_count - before_count) / before_count) * 100
        else:
            percent_change = 100 if after_count > 0 else 0

        return {
            'found': True,
            'event': {
                'id': event.event_id,
                'type': event.event_type,
                'name': event.event_name,
                'start_time': event.start_time.isoformat()
            },
            'entity': entity.canonical_form,
            'mentions_before': before_count,
            'mentions_after': after_count,
            'change': round(percent_change, 2),
            'impact': 'increased' if percent_change > 20 else 'decreased' if percent_change < -20 else 'stable'
        }


# Global instance
event_detection_service = EventDetectionService()
