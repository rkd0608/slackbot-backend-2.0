"""GPT-4 based comprehensive query analysis for enhanced retrieval"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger
import json
import re

logger = get_logger(__name__)


class QueryAnalyzer:
    """Uses GPT-4 to comprehensively analyze user queries"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.query_analyzer_model

    async def analyze(self, query: str) -> Dict[str, Any]:
        """Perform comprehensive GPT-4 based query analysis"""

        if not settings.enable_llm_query_analysis:
            # Fallback to basic analysis
            return self._basic_analysis(query)

        try:
            # Build analysis prompt
            prompt = self._build_analysis_prompt(query)

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are a query analysis specialist. Your task is to parse user search queries and extract structured metadata to enable precise information retrieval across Slack, GitHub, and other sources.

Your analysis directly impacts search quality - accurate extraction means users find exactly what they need.

Always return valid, complete JSON matching the specified schema."""
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            # Parse JSON response
            content = response.choices[0].message.content
            analysis = json.loads(content)

            # Post-process and validate
            processed_analysis = self._process_analysis(analysis, query)

            logger.info(
                "query_analyzed_llm",
                query=query,
                query_type=processed_analysis.get("query_type"),
                intents=processed_analysis.get("intents"),
                channels_count=len(processed_analysis.get("channels", [])),
                users_count=len(processed_analysis.get("users", [])),
                has_temporal=bool(processed_analysis.get("temporal"))
            )

            return processed_analysis

        except Exception as e:
            logger.error("query_analysis_error", error=str(e), query=query)
            # Fallback to basic analysis
            return self._basic_analysis(query)

    def _build_analysis_prompt(self, query: str) -> str:
        """Build structured analysis prompt for GPT-4"""

        return f"""<task>
Analyze this Slack workspace search query to extract structured information for optimal retrieval.
Your analysis will be used to search across Slack messages, GitHub code, and files.
</task>

<query>
{query}
</query>

<output_schema>
Return a JSON object with these fields:

1. query_type: Classify as ONE of these:
   - "factual" - seeks specific facts/information
   - "summary" - wants overview/summary of discussions
   - "code" - needs code/implementations
   - "troubleshooting" - debugging/problem-solving
   - "timeline" - chronological events
   - "people" - attribution (who said/did what)
   - "files" - documents/attachments

2. intents: Array of specific intents
   Examples: ["find_code", "understand_context", "get_summary"]

3. entities: Array of key entities with type
   Format: [{{"text": "entity name", "type": "category"}}]
   Types: object, issue, technology, project, feature, concept

4. channels: Channel names WITHOUT # prefix
   Examples: "#engineering" → ["engineering"]

5. users: User names WITHOUT @ prefix
   Examples: "@john" → ["john"]

6. temporal: Time constraints (null if none)
   Format: {{
     "reference": "last week" | "yesterday" | null,
     "relative_days": -7 | null,
     "start_date": "YYYY-MM-DD" | null,
     "end_date": "YYYY-MM-DD" | null
   }}

7. has_code_intent: boolean
   True if query seeks code/implementation

8. code_languages: Array of mentioned languages
   Examples: ["python", "sql", "javascript"]

9. keywords: 3-5 most important search terms

10. search_scope: "narrow" | "broad"
    narrow = specific entity/file, broad = general topic

11. requires_aggregation: boolean
    True if needs combining multiple messages
</output_schema>

<examples>
<example>
Query: "What did John say about the authentication bug last week?"
Output:
{{
  "query_type": "people",
  "intents": ["find_person_statements", "temporal_filter"],
  "entities": [
    {{"text": "authentication bug", "type": "issue"}},
    {{"text": "authentication", "type": "technology"}}
  ],
  "channels": [],
  "users": ["john"],
  "temporal": {{
    "reference": "last week",
    "relative_days": -7,
    "start_date": null,
    "end_date": null
  }},
  "has_code_intent": false,
  "code_languages": [],
  "keywords": ["authentication", "bug", "john"],
  "search_scope": "narrow",
  "requires_aggregation": false
}}
</example>

<example>
Query: "Show me the Python code for the email service"
Output:
{{
  "query_type": "code",
  "intents": ["find_code", "find_implementation"],
  "entities": [
    {{"text": "email service", "type": "feature"}},
    {{"text": "Python", "type": "technology"}}
  ],
  "channels": [],
  "users": [],
  "temporal": null,
  "has_code_intent": true,
  "code_languages": ["python"],
  "keywords": ["email", "service", "code"],
  "search_scope": "narrow",
  "requires_aggregation": false
}}
</example>

<example>
Query: "Summarize the discussion in #engineering about database migration"
Output:
{{
  "query_type": "summary",
  "intents": ["get_summary", "find_discussion"],
  "entities": [
    {{"text": "database migration", "type": "concept"}},
    {{"text": "database", "type": "technology"}}
  ],
  "channels": ["engineering"],
  "users": [],
  "temporal": null,
  "has_code_intent": false,
  "code_languages": [],
  "keywords": ["database", "migration", "discussion"],
  "search_scope": "broad",
  "requires_aggregation": true
}}
</example>
</examples>

<instructions>
1. Read the query carefully
2. Identify the primary intent (what does the user ultimately want?)
3. Extract all relevant entities, channels, users
4. Determine temporal scope (when)
5. Classify query type based on what output the user needs
6. Return ONLY valid JSON (no markdown, no explanations)
</instructions>"""

    def _process_analysis(self, analysis: Dict[str, Any], original_query: str) -> Dict[str, Any]:
        """Post-process and validate GPT-4 analysis"""

        # FIRST: Extract Slack-formatted channel IDs from original query (BEFORE lowercasing!)
        # Format: <#CHANNEL_ID|channel_name> or <#CHANNEL_ID|> - we want the CHANNEL_ID in uppercase
        slack_channel_ids = re.findall(r'<#([A-Z0-9]+)(?:\|[^>]*)?>', original_query)

        # THEN: Get LLM-extracted channel names (lowercased)
        llm_channels = [ch.lower().strip() for ch in analysis.get("channels", [])]

        # Combine: prioritize Slack IDs, then add LLM-extracted names
        all_channels = list(set(slack_channel_ids + llm_channels))

        logger.info(
            "channel_extraction_debug",
            original_query=original_query[:100],
            slack_channel_ids=slack_channel_ids,
            llm_channels=llm_channels,
            all_channels=all_channels
        )

        # Ensure all required fields exist
        processed = {
            "original_query": original_query,
            "query_type": analysis.get("query_type", "factual"),
            "intents": analysis.get("intents", []),
            "entities": analysis.get("entities", []),
            "channels": all_channels,
            "users": [u.lower().strip() for u in analysis.get("users", [])],
            "temporal": self._process_temporal(analysis.get("temporal")),
            "has_code_intent": analysis.get("has_code_intent", False),
            "code_languages": analysis.get("code_languages", []),
            "keywords": analysis.get("keywords", []),
            "search_scope": analysis.get("search_scope", "broad"),
            "requires_aggregation": analysis.get("requires_aggregation", False)
        }

        # Normalize query for search
        processed["normalized_query"] = self._normalize_query(original_query)

        return processed

    def _process_temporal(self, temporal: Optional[Dict]) -> Optional[Dict[str, Any]]:
        """Process and calculate temporal constraints"""

        if not temporal or not isinstance(temporal, dict):
            return None

        result = {
            "reference": temporal.get("reference"),
            "relative_days": temporal.get("relative_days")
        }

        # Calculate actual dates
        now = datetime.now()

        # If relative_days provided, calculate dates
        if temporal.get("relative_days") is not None:
            days = temporal["relative_days"]
            if days == 0:  # Today - special case for full day range
                # Start of today (00:00:00)
                result["start_date"] = now.replace(hour=0, minute=0, second=0, microsecond=0)
                # End of today (23:59:59)
                result["end_date"] = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            elif days < 0:  # Past
                result["start_date"] = now + timedelta(days=days)
                result["end_date"] = now
            else:  # Future (positive days)
                result["start_date"] = now
                result["end_date"] = now + timedelta(days=days)

        # If explicit dates provided, parse them
        elif temporal.get("start_date") or temporal.get("end_date"):
            if temporal.get("start_date"):
                try:
                    result["start_date"] = datetime.fromisoformat(temporal["start_date"])
                except:
                    pass
            if temporal.get("end_date"):
                try:
                    result["end_date"] = datetime.fromisoformat(temporal["end_date"])
                except:
                    pass

        # Return None if no actual dates calculated
        if "start_date" not in result and "end_date" not in result:
            return None

        return result

    def _normalize_query(self, query: str) -> str:
        """Normalize query for better matching"""
        # Remove extra spaces
        query = re.sub(r'\s+', ' ', query).strip()
        # Remove common stop words for better keyword matching
        stop_words = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        words = query.split()
        words = [w for w in words if w.lower() not in stop_words or len(words) <= 3]
        return ' '.join(words)

    def _basic_analysis(self, query: str) -> Dict[str, Any]:
        """Fallback regex-based analysis when LLM is disabled"""

        return {
            "original_query": query,
            "normalized_query": self._normalize_query(query),
            "query_type": self._classify_basic_type(query),
            "intents": [],
            "entities": [],
            "channels": self._extract_channels_regex(query),
            "users": self._extract_users_regex(query),
            "temporal": None,
            "has_code_intent": bool(re.search(r'\b(code|function|class|implementation|snippet)\b', query, re.I)),
            "code_languages": [],
            "keywords": [w for w in query.split() if len(w) > 3][:5],
            "search_scope": "broad",
            "requires_aggregation": False
        }

    def _classify_basic_type(self, query: str) -> str:
        """Basic query type classification"""
        query_lower = query.lower()

        if re.search(r'\b(summary|summarize|overview|discuss)\b', query_lower):
            return "summary"
        elif re.search(r'\b(code|function|implementation|script)\b', query_lower):
            return "code"
        elif re.search(r'\b(when|timeline|history|sequence)\b', query_lower):
            return "timeline"
        elif re.search(r'\b(who|said|mentioned|posted)\b', query_lower):
            return "people"
        elif re.search(r'\b(file|document|attachment|shared)\b', query_lower):
            return "files"
        elif re.search(r'\b(error|bug|issue|problem|fix)\b', query_lower):
            return "troubleshooting"
        else:
            return "factual"

    def _extract_channels_regex(self, query: str) -> List[str]:
        """Extract channel names using regex"""
        channels = []

        # Match #channel format
        hash_channels = re.findall(r'#([\w-]+)', query)
        channels.extend(hash_channels)

        # Match "in the X channel" format
        in_channels = re.findall(r'in\s+(?:the\s+)?([\w-]+)\s+channel', query, re.I)
        channels.extend(in_channels)

        return [ch.lower() for ch in channels]

    def _extract_users_regex(self, query: str) -> List[str]:
        """Extract user mentions using regex"""
        users = []

        # Match @user format
        at_users = re.findall(r'@([\w-]+)', query)
        users.extend(at_users)

        # Match "from/by UserName" format
        name_users = re.findall(r'(?:from|by)\s+([A-Z][a-z]+)', query)
        users.extend(name_users)

        return [u.lower() for u in users]


# Global instance
query_analyzer = QueryAnalyzer()
