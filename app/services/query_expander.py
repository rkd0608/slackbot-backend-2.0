"""Query expansion for better retrieval coverage"""
from typing import List
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueryExpander:
    """Expands queries with synonyms and related terms"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def expand_query(self, query: str, max_expansions: int = 2) -> List[str]:
        """
        Generate query variations for better retrieval

        Args:
            query: Original search query
            max_expansions: Maximum number of alternative phrasings

        Returns:
            List of query variations [original_query, expansion1, expansion2, ...]
        """
        # Check if feature is enabled
        if not getattr(settings, 'enable_query_expansion', False):
            return [query]

        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": """Generate alternative phrasings of a query for better search recall.

Rules:
1. Use synonyms and related terms
2. Keep the same intent
3. Make variations concise
4. Return 2-3 alternatives max
5. Don't just reorder words
6. Each alternative on a new line starting with "- "

Examples:
Query: "deployment issues"
Expansions:
- release problems
- production rollout errors

Query: "how to configure Redis"
Expansions:
- Redis setup guide
- setting up Redis cache
"""
                    },
                    {
                        "role": "user",
                        "content": f"Query: {query}\n\nGenerate {max_expansions} alternative phrasings:"
                    }
                ],
                temperature=0.3,
                max_tokens=100
            )

            content = response.choices[0].message.content

            # Parse expansions (expecting newline-separated with "- " prefix)
            expansions = [
                line.strip().lstrip('- ').strip()
                for line in content.split('\n')
                if line.strip() and line.strip().startswith('-')
            ]

            # Always include original first
            result = [query] + expansions[:max_expansions]

            logger.info("query_expanded", original=query, expansions=len(result)-1)
            return result

        except Exception as e:
            logger.error("query_expansion_error", error=str(e))
            return [query]


# Global instance
query_expander = QueryExpander()
