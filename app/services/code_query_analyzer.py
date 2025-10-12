"""Code query analyzer to detect code-specific intent in user queries"""
from typing import Dict, Any, Optional
import re
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CodeQueryAnalyzer:
    """Analyze if query is code-specific and extract code intent"""

    # Keywords indicating code-specific queries
    CODE_KEYWORDS = {
        "find_function": ["function", "def", "method", "procedure", "func"],
        "find_class": ["class", "object", "type", "struct", "interface"],
        "find_usage": ["usage", "example", "how to use", "sample", "demo"],
        "understand_code": ["explain", "what does", "how does", "understand", "meaning"],
        "find_implementation": ["implementation", "code for", "source", "implement", "code that", "script", "snippet"],
        "find_import": ["import", "dependency", "package", "module", "library"]
    }

    # Language-specific keywords
    LANGUAGE_KEYWORDS = {
        "python": ["python", "py", "django", "flask", "pandas", "numpy"],
        "javascript": ["javascript", "js", "react", "node", "npm", "typescript", "ts"],
        "java": ["java", "spring", "maven", "gradle"],
        "go": ["golang", "go"],
        "sql": ["sql", "query", "database", "select", "table"],
        "rust": ["rust", "cargo"],
        "cpp": ["c++", "cpp"],
    }

    def analyze_code_intent(self, query: str) -> Dict[str, Any]:
        """
        Determine if query is asking for code and extract intent

        Returns:
            {
                "is_code_query": bool,
                "confidence": float (0-1),
                "code_intent": str,  # find_function, find_class, etc.
                "language_hint": str or None,
                "entity_type": str,  # function, class, import, variable
                "entity_name": str or None,
                "concepts": list,
                "search_strategy": str  # structural, semantic, hybrid
            }
        """
        try:
            query_lower = query.lower()

            # Step 1: Detect if it's a code query
            is_code, confidence = self._detect_code_query(query_lower)

            if not is_code:
                return {
                    "is_code_query": False,
                    "confidence": 0.0,
                    "code_intent": None,
                    "language_hint": None,
                    "entity_type": None,
                    "entity_name": None,
                    "concepts": [],
                    "search_strategy": "semantic"
                }

            # Step 2: Determine specific intent
            code_intent = self._determine_intent(query_lower)

            # Step 3: Extract language hint
            language_hint = self._extract_language_hint(query_lower)

            # Step 4: Extract entity type and name
            entity_type, entity_name = self._extract_entity(query_lower, code_intent)

            # Step 5: Extract concepts
            concepts = self._extract_concepts(query)

            # Step 6: Determine search strategy
            search_strategy = self._determine_strategy(code_intent, entity_name)

            result = {
                "is_code_query": True,
                "confidence": confidence,
                "code_intent": code_intent,
                "language_hint": language_hint,
                "entity_type": entity_type,
                "entity_name": entity_name,
                "concepts": concepts,
                "search_strategy": search_strategy
            }

            logger.info(
                "code_intent_analyzed",
                query=query[:100],
                **result
            )

            return result

        except Exception as e:
            logger.error("code_intent_analysis_error", error=str(e))
            return {
                "is_code_query": False,
                "confidence": 0.0,
                "code_intent": None,
                "language_hint": None,
                "entity_type": None,
                "entity_name": None,
                "concepts": [],
                "search_strategy": "semantic"
            }

    def _detect_code_query(self, query_lower: str) -> tuple[bool, float]:
        """
        Detect if query is about code

        Returns:
            (is_code_query, confidence_score)
        """
        confidence = 0.0

        # Check for explicit code keywords
        for intent, keywords in self.CODE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    confidence += 0.3

        # Check for language mentions
        for lang, keywords in self.LANGUAGE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    confidence += 0.2

        # Check for code-related patterns
        code_patterns = [
            r'\bfunction\s+\w+',
            r'\bclass\s+\w+',
            r'\bdef\s+\w+',
            r'\bimport\s+\w+',
            r'`[^`]+`',  # Inline code
            r'```',  # Code block
        ]

        for pattern in code_patterns:
            if re.search(pattern, query_lower):
                confidence += 0.25

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # Threshold: 0.3 or higher means it's a code query
        is_code = confidence >= 0.3

        return is_code, confidence

    def _determine_intent(self, query_lower: str) -> str:
        """Determine specific code intent"""
        # Check each intent type
        for intent, keywords in self.CODE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return intent

        # Default
        return "find_code"

    def _extract_language_hint(self, query_lower: str) -> Optional[str]:
        """Extract programming language hint from query"""
        for lang, keywords in self.LANGUAGE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    # Normalize language name
                    if lang == "javascript":
                        # Check if it's TypeScript
                        if "typescript" in query_lower or "ts" in query_lower.split():
                            return "typescript"
                    return lang

        return None

    def _extract_entity(
        self,
        query_lower: str,
        code_intent: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Extract entity type and name from query

        Returns:
            (entity_type, entity_name)
        """
        entity_type = None
        entity_name = None

        # Determine entity type from intent
        if code_intent == "find_function":
            entity_type = "function"
        elif code_intent == "find_class":
            entity_type = "class"
        elif code_intent == "find_import":
            entity_type = "import"

        # Try to extract entity name
        # Pattern: "function called X", "class named Y", "X function", "Y class"
        patterns = [
            r'function\s+(?:called|named)?\s*(\w+)',
            r'(\w+)\s+function',
            r'class\s+(?:called|named)?\s*(\w+)',
            r'(\w+)\s+class',
            r'def\s+(\w+)',
            r'import\s+(\w+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                entity_name = match.group(1)
                break

        return entity_type, entity_name

    def _extract_concepts(self, query: str) -> list:
        """Extract key concepts from query"""
        # Remove common words
        stop_words = {
            'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'is', 'are', 'was', 'were',
            'show', 'find', 'get', 'give', 'me', 'how', 'what',
            'code', 'function', 'class'
        }

        words = query.lower().split()
        concepts = [
            word for word in words
            if len(word) > 3 and word not in stop_words
        ]

        return concepts[:5]  # Top 5 concepts

    def _determine_strategy(
        self,
        code_intent: str,
        entity_name: Optional[str]
    ) -> str:
        """
        Determine search strategy based on intent and entity

        Strategies:
        - structural: Search by function/class name (fast, precise)
        - semantic: Search by meaning (better for understanding)
        - hybrid: Combine both (best results)
        """
        # If we have an entity name, prefer structural search
        if entity_name:
            return "structural"

        # For understanding queries, use semantic
        if code_intent == "understand_code":
            return "semantic"

        # For usage/example queries, use hybrid
        if code_intent in ["find_usage", "find_implementation"]:
            return "hybrid"

        # Default to hybrid
        return "hybrid"


# Global instance
code_query_analyzer = CodeQueryAnalyzer()
