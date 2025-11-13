"""Query understanding and classification service"""
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueryService:
    """Analyzes and classifies user queries"""

    # Intent types
    INTENT_FACTUAL = "factual"
    INTENT_CODE = "code"
    INTENT_SUMMARY = "summary"
    INTENT_TIMELINE = "timeline"
    INTENT_WHO = "who"
    INTENT_COMPARISON = "comparison"
    INTENT_HOWTO = "howto"

    # Regex patterns
    CODE_KEYWORDS = r'\b(function|class|method|sql|query|code|implementation|snippet|script)\b'
    TIMELINE_KEYWORDS = r'\b(when|timeline|history|happened|sequence|chronology)\b'
    WHO_KEYWORDS = r'\b(who|whose|author|contributor|mentioned|said)\b'
    COMPARISON_KEYWORDS = r'\b(compare|comparison|versus|vs|difference|better)\b'
    SUMMARY_KEYWORDS = r'\b(summary|summarize|overview|recap|digest)\b'
    HOWTO_KEYWORDS = r'\b(how to|how do|how can|guide|tutorial|steps)\b'

    # Temporal expressions
    TEMPORAL_PATTERNS = {
        'today': lambda: datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        'yesterday': lambda: (datetime.utcnow() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        'this week': lambda: datetime.utcnow() - timedelta(days=7),
        'last week': lambda: datetime.utcnow() - timedelta(days=14),
        'this month': lambda: datetime.utcnow() - timedelta(days=30),
        'last month': lambda: datetime.utcnow() - timedelta(days=60),
        'this year': lambda: datetime.utcnow() - timedelta(days=365),
    }

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.use_llm_entities = getattr(settings, 'enable_llm_entity_extraction', False)

    async def analyze_query(self, query_text: str) -> Dict[str, Any]:
        """Comprehensive query analysis"""

        # Use GPT-4 based query analyzer if enabled
        if getattr(settings, 'enable_llm_query_analysis', False):
            from app.services.query_analyzer import query_analyzer
            return await query_analyzer.analyze(query_text)

        # Fallback to regex-based analysis
        # Use LLM for entity extraction if enabled
        if self.use_llm_entities:
            entities = await self._extract_entities_llm(query_text)
        else:
            entities = self._extract_entities(query_text)

        analysis = {
            "original_query": query_text,
            "normalized_query": self._normalize_query(query_text),
            "intents": self._classify_intent(query_text),
            "entities": entities,
            "temporal": self._extract_temporal(query_text),
            "channels": self._extract_channels(query_text),
            "users": self._extract_user_mentions(query_text),
            "has_code_intent": self._has_code_intent(query_text),
            "question_type": self._determine_question_type(query_text)
        }

        logger.info(
            "query_analyzed",
            intents=analysis["intents"],
            has_temporal=bool(analysis["temporal"]),
            has_channels=bool(analysis["channels"])
        )

        return analysis

    def _normalize_query(self, query: str) -> str:
        """Normalize query text"""
        # Convert to lowercase
        normalized = query.lower()

        # Expand common abbreviations
        abbreviations = {
            'db': 'database',
            'k8s': 'kubernetes',
            'api': 'application programming interface',
            'ui': 'user interface',
            'ux': 'user experience',
            'ml': 'machine learning',
            'ai': 'artificial intelligence',
            'ci': 'continuous integration',
            'cd': 'continuous deployment',
        }

        for abbr, full in abbreviations.items():
            normalized = re.sub(r'\b' + abbr + r'\b', full, normalized)

        return normalized

    def _classify_intent(self, query: str) -> List[str]:
        """Classify query intent (can have multiple intents)"""
        intents = []
        query_lower = query.lower()

        # Check for different intent patterns
        if re.search(self.WHO_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_WHO)

        if re.search(self.CODE_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_CODE)

        if re.search(self.TIMELINE_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_TIMELINE)

        if re.search(self.COMPARISON_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_COMPARISON)

        if re.search(self.SUMMARY_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_SUMMARY)

        if re.search(self.HOWTO_KEYWORDS, query_lower, re.IGNORECASE):
            intents.append(self.INTENT_HOWTO)

        # Default to factual if no specific intent detected
        if not intents:
            intents.append(self.INTENT_FACTUAL)

        return intents

    def _extract_entities(self, query: str) -> List[Dict[str, str]]:
        """Extract named entities from query"""
        entities = []

        # Extract quoted phrases (often important entities)
        quoted = re.findall(r'"([^"]+)"', query)
        for q in quoted:
            entities.append({"text": q, "type": "exact_phrase"})

        # Extract technical terms (capitalized words, acronyms)
        tech_terms = re.findall(r'\b[A-Z][A-Za-z0-9]+\b', query)
        for term in tech_terms:
            if len(term) > 1:  # Avoid single letters
                entities.append({"text": term, "type": "technical"})

        # Extract JIRA-style tickets
        tickets = re.findall(r'\b[A-Z]+-\d+\b', query)
        for ticket in tickets:
            entities.append({"text": ticket, "type": "ticket"})

        # Extract URLs
        urls = re.findall(r'https?://[^\s]+', query)
        for url in urls:
            entities.append({"text": url, "type": "url"})

        return entities

    def _extract_temporal(self, query: str) -> Optional[Dict[str, Any]]:
        """Extract temporal information from query"""
        query_lower = query.lower()

        # Check for relative temporal expressions
        for phrase, date_func in self.TEMPORAL_PATTERNS.items():
            if phrase in query_lower:
                date = date_func()
                return {
                    "phrase": phrase,
                    "start_date": date,
                    "end_date": datetime.utcnow()
                }

        # Check for absolute dates (YYYY-MM-DD)
        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', query)
        if date_match:
            try:
                date = datetime(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3))
                )
                return {
                    "phrase": "absolute_date",
                    "start_date": date,
                    "end_date": date + timedelta(days=1)
                }
            except ValueError:
                pass

        # Check for "after X" or "before X" patterns
        after_match = re.search(r'after\s+(\w+\s+\d+)', query_lower)
        if after_match:
            return {
                "phrase": f"after {after_match.group(1)}",
                "start_date": None,  # Would need date parsing
                "operator": "after"
            }

        before_match = re.search(r'before\s+(\w+\s+\d+)', query_lower)
        if before_match:
            return {
                "phrase": f"before {before_match.group(1)}",
                "end_date": None,  # Would need date parsing
                "operator": "before"
            }

        return None

    def _extract_channels(self, query: str) -> List[str]:
        """Extract channel references from query"""
        channels = []

        # FIRST: Extract Slack-formatted channel mentions (BEFORE lowercasing!)
        # Format: <#CHANNEL_ID|channel_name> or <#CHANNEL_ID|> or <#CHANNEL_ID>
        # We want to keep the CHANNEL_ID in uppercase to match Pinecone
        slack_channel_mentions = re.findall(r'<#([A-Z0-9]+)(?:\|[^>]*)?>', query)
        channels.extend(slack_channel_mentions)

        # THEN: Extract #channel-name format (from lowercased query)
        channel_mentions = re.findall(r'#([a-z0-9-_]+)', query.lower())
        channels.extend(channel_mentions)

        # Extract "in channel" phrases
        in_channel = re.findall(r'in\s+(?:the\s+)?([a-z0-9-_]+)\s+channel', query.lower())
        channels.extend(in_channel)

        # Extract channel keywords
        channel_keywords = re.findall(r'channel[:\s]+([a-z0-9-_]+)', query.lower())
        channels.extend(channel_keywords)

        return list(set(channels))  # Deduplicate

    def _extract_user_mentions(self, query: str) -> List[str]:
        """Extract user mentions from query"""
        # Extract @username format
        mentions = re.findall(r'@([a-zA-Z0-9._-]+)', query)

        # Extract "by user" phrases
        by_user = re.findall(r'by\s+([a-zA-Z0-9._-]+)', query.lower())
        mentions.extend(by_user)

        return list(set(mentions))

    def _has_code_intent(self, query: str) -> bool:
        """Check if query is looking for code"""
        code_indicators = [
            'code', 'function', 'method', 'class', 'implementation',
            'snippet', 'script', 'query', 'sql', 'api call', 'example'
        ]

        query_lower = query.lower()
        return any(indicator in query_lower for indicator in code_indicators)

    def _determine_question_type(self, query: str) -> str:
        """Determine the type of question"""
        query_lower = query.lower().strip()

        if query_lower.startswith(('what', 'what\'s', 'what is')):
            return "what"
        elif query_lower.startswith(('who', 'who\'s', 'who is')):
            return "who"
        elif query_lower.startswith(('when', 'when\'s', 'when did', 'when was')):
            return "when"
        elif query_lower.startswith(('where', 'where\'s', 'where is')):
            return "where"
        elif query_lower.startswith(('why', 'why did', 'why is')):
            return "why"
        elif query_lower.startswith(('how', 'how to', 'how do', 'how can')):
            return "how"
        elif query_lower.startswith(('which', 'which is')):
            return "which"
        elif '?' in query:
            return "question"
        else:
            return "statement"

    async def expand_query(self, query: str) -> List[str]:
        """Generate query expansions for better retrieval"""
        expansions = [query]

        # Add normalized version
        normalized = self._normalize_query(query)
        if normalized != query.lower():
            expansions.append(normalized)

        # Add version without question words for better matching
        without_qwords = re.sub(
            r'\b(what|who|when|where|why|how|which|is|are|was|were|did|does|do|can|could|would|should)\b\s*',
            '',
            query.lower(),
            flags=re.IGNORECASE
        ).strip()

        if without_qwords and without_qwords != query.lower():
            expansions.append(without_qwords)

        return list(set(expansions))

    async def _extract_entities_llm(self, query: str) -> List[Dict[str, str]]:
        """
        Use LLM to extract entities with better accuracy
        Falls back to regex-based extraction if LLM fails
        """
        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": """Extract named entities from Slack workspace queries.

Categories:
- person: Names of people (@john, John Doe)
- channel: Slack channels (#engineering, engineering channel)
- project: Project names (Project Apollo, Q2 Dashboard)
- technology: Technical terms (Redis, Kubernetes, API)
- ticket: JIRA tickets (PROJ-123)
- temporal: Time references (last week, yesterday)
- file: File references (HTML file, config.yaml)

Return JSON with "entities" array: {"entities": [{"text": "Redis", "type": "technology"}, ...]}
Return {"entities": []} if no entities found."""
                    },
                    {
                        "role": "user",
                        "content": f"Query: {query}\n\nExtract entities as JSON:"
                    }
                ],
                temperature=0,
                max_tokens=200,
                response_format={"type": "json_object"}
            )

            import json
            result = json.loads(response.choices[0].message.content)
            entities = result.get("entities", [])

            logger.info("llm_entity_extraction", query=query, count=len(entities))
            return entities

        except Exception as e:
            logger.error("llm_entity_extraction_error", error=str(e))
            # Fall back to regex
            return self._extract_entities(query)


# Global query service instance
query_service = QueryService()
