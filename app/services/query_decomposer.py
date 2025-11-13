"""
Query Decomposition Service

Breaks complex queries into simpler sub-queries for better retrieval.

Examples:
- "Show me PRs and discussions about authentication"
  → ["Show me GitHub PRs about authentication", "Show me Slack discussions about authentication"]

- "What did John say about the deployment and what code changes were made?"
  → ["What did John say about the deployment?", "What code changes were made for the deployment?"]

- "Find issues related to performance in the API"
  → ["Find GitHub issues about API performance", "Find code related to API performance"]
"""
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
import json

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueryDecomposer:
    """Decomposes complex queries into simpler sub-queries"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.enabled = getattr(settings, 'enable_query_decomposition', True)

    async def decompose_query(
        self,
        query: str,
        query_analysis: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Decompose a complex query into sub-queries

        Args:
            query: Original user query
            query_analysis: Optional pre-analyzed query metadata

        Returns:
            Dict with:
            - needs_decomposition: bool
            - sub_queries: List[Dict] with strategy info
            - original_query: str
            - decomposition_reason: str
        """
        if not self.enabled:
            return {
                'needs_decomposition': False,
                'sub_queries': [{'query': query, 'type': 'original'}],
                'original_query': query
            }

        try:
            # Check if query needs decomposition
            needs_decomposition = self._should_decompose(query, query_analysis)

            if not needs_decomposition:
                return {
                    'needs_decomposition': False,
                    'sub_queries': [{'query': query, 'type': 'original', 'weight': 1.0}],
                    'original_query': query
                }

            # Decompose using LLM
            decomposition = await self._llm_decompose(query, query_analysis)

            logger.info(
                "query_decomposed",
                original=query,
                sub_count=len(decomposition['sub_queries'])
            )

            return decomposition

        except Exception as e:
            logger.error("query_decomposition_error", error=str(e))
            # Fallback: return original query
            return {
                'needs_decomposition': False,
                'sub_queries': [{'query': query, 'type': 'original', 'weight': 1.0}],
                'original_query': query,
                'error': str(e)
            }

    def _should_decompose(
        self,
        query: str,
        query_analysis: Optional[Dict[str, Any]]
    ) -> bool:
        """
        Determine if query should be decomposed

        Heuristics:
        - Contains "and" or "or" conjunctions
        - Multiple question words
        - Multiple source types mentioned (Slack + GitHub)
        - Long queries (>50 words)
        - Multiple entities mentioned
        """
        query_lower = query.lower()

        # Check for conjunctions
        has_and = ' and ' in query_lower
        has_or = ' or ' in query_lower

        # Check for multiple questions
        question_words = ['what', 'who', 'when', 'where', 'why', 'how', 'which']
        question_count = sum(1 for word in question_words if word in query_lower.split())

        # Check for source mentions
        source_mentions = ['slack', 'github', 'pr', 'pull request', 'issue', 'code', 'message', 'discussion']
        source_count = sum(1 for mention in source_mentions if mention in query_lower)

        # Check word count
        word_count = len(query.split())

        # Check entity count
        entity_count = 0
        if query_analysis and query_analysis.get('entities'):
            entity_count = len(query_analysis['entities'])

        # Decompose if:
        # 1. Has multiple conjunctions
        # 2. Multiple question words + long query
        # 3. Multiple source types mentioned
        # 4. Very long query
        # 5. Many entities

        if (has_and or has_or) and (source_count >= 2 or question_count >= 2):
            return True

        if question_count >= 2 and word_count > 20:
            return True

        if source_count >= 2 and word_count > 15:
            return True

        if word_count > 50:
            return True

        if entity_count >= 4:
            return True

        return False

    async def _llm_decompose(
        self,
        query: str,
        query_analysis: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Use LLM to decompose query into sub-queries

        Returns structured decomposition with search strategies
        """
        # Build context from query analysis
        analysis_context = ""
        if query_analysis:
            entities = query_analysis.get('entities', [])
            intents = query_analysis.get('intents', [])

            if entities:
                entity_str = ", ".join([e.get('text', '') for e in entities[:5]])
                analysis_context += f"\nEntities mentioned: {entity_str}"

            if intents:
                analysis_context += f"\nQuery intents: {', '.join(intents)}"

        prompt = f"""<task>
Break down complex queries into simpler sub-queries for optimal multi-source search.
Your decomposition enables parallel search across Slack and GitHub, then intelligent result merging.
</task>

<original_query>
{query}
</original_query>

<context>
{analysis_context}
</context>

<strategy>
Decompose if query:
1. Targets multiple sources (Slack + GitHub)
2. Has multiple distinct questions
3. Combines different search intents

Create 2-4 focused sub-queries that:
- Target specific sources or aspects
- Preserve entities and context
- Can be searched independently
- Together answer the full question
</strategy>

<available_options>
Sources: slack, github, all

Search Strategies:
- semantic: Vector similarity (conceptual queries)
- keyword: Exact matching (specific terms)
- hybrid: Combined semantic + keyword (default)
- graph: Relationship traversal
</available_options>

<output_format>
{{
  "needs_decomposition": boolean,
  "decomposition_reason": "explanation",
  "sub_queries": [
    {{
      "query": "text",
      "type": "source_specific|aspect_focused|temporal",
      "source_preference": "slack|github|all",
      "search_strategy": "semantic|keyword|hybrid|graph",
      "weight": 0.0-1.0,
      "rationale": "explanation"
    }}
  ]
}}
</output_format>

<examples>

Query: "Show me PRs and discussions about authentication"
{{
  "needs_decomposition": true,
  "decomposition_reason": "Query targets two different sources",
  "sub_queries": [
    {{
      "query": "GitHub pull requests about authentication",
      "type": "source_specific",
      "source_preference": "github",
      "search_strategy": "hybrid",
      "weight": 0.6,
      "rationale": "User explicitly asked for PRs"
    }},
    {{
      "query": "Slack discussions about authentication",
      "type": "source_specific",
      "source_preference": "slack",
      "search_strategy": "semantic",
      "weight": 0.4,
      "rationale": "User asked for discussions"
    }}
  ]
}}

Query: "What did John say about deployment and what code was changed?"
{{
  "needs_decomposition": true,
  "decomposition_reason": "Query has two distinct questions",
  "sub_queries": [
    {{
      "query": "What did John say about deployment",
      "type": "aspect_focused",
      "source_preference": "slack",
      "search_strategy": "hybrid",
      "weight": 0.5,
      "rationale": "First part asks about person's statements"
    }},
    {{
      "query": "What code changes were made for deployment",
      "type": "aspect_focused",
      "source_preference": "github",
      "search_strategy": "hybrid",
      "weight": 0.5,
      "rationale": "Second part asks about code changes"
    }}
  ]
}}

Now decompose the query above:"""

        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are a query decomposition specialist for multi-source knowledge bases.

Your task is to break complex queries into focused sub-queries that can be searched independently across Slack, GitHub, and other sources, then merged intelligently.

Effective decomposition improves search recall and precision by allowing parallel, targeted searches. Return only valid JSON."""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)
        result['original_query'] = query

        # Validate weights sum to reasonable range (0.8-1.2)
        total_weight = sum(sq.get('weight', 1.0) for sq in result.get('sub_queries', []))
        if total_weight > 0:
            # Normalize weights to sum to 1.0
            for sq in result['sub_queries']:
                sq['weight'] = sq.get('weight', 1.0) / total_weight

        return result

    async def merge_results(
        self,
        sub_query_results: List[Dict[str, Any]],
        original_query: str
    ) -> List[Dict[str, Any]]:
        """
        Merge results from multiple sub-queries

        Args:
            sub_query_results: List of result sets from each sub-query
            original_query: The original complex query

        Returns:
            Merged and deduplicated results with adjusted scores
        """
        try:
            all_results = []
            seen_ids = set()

            # Collect all results with adjusted scores
            for sub_result in sub_query_results:
                weight = sub_result.get('weight', 1.0)
                results = sub_result.get('results', [])

                for result in results:
                    result_id = result.get('canonical_id') or result.get('message_id')

                    if not result_id:
                        continue

                    # Deduplicate
                    if result_id in seen_ids:
                        # Update score if this sub-query gave it higher weight
                        for existing in all_results:
                            existing_id = existing.get('canonical_id') or existing.get('message_id')
                            if existing_id == result_id:
                                # Boost score if multiple sub-queries found it
                                existing['score'] = min(1.0, existing.get('score', 0) + (result.get('score', 0) * weight * 0.5))
                                break
                        continue

                    seen_ids.add(result_id)

                    # Adjust score by sub-query weight
                    result['score'] = result.get('score', 0.5) * weight
                    result['sub_query'] = sub_result.get('query')

                    all_results.append(result)

            # Sort by adjusted score
            all_results.sort(key=lambda x: x.get('score', 0), reverse=True)

            logger.info(
                "sub_query_results_merged",
                sub_queries=len(sub_query_results),
                total_results=len(all_results),
                deduplicated=sum(len(sr.get('results', [])) for sr in sub_query_results) - len(all_results)
            )

            return all_results

        except Exception as e:
            logger.error("merge_results_error", error=str(e))
            # Fallback: return first result set
            if sub_query_results:
                return sub_query_results[0].get('results', [])
            return []


# Global query decomposer instance
query_decomposer = QueryDecomposer()
