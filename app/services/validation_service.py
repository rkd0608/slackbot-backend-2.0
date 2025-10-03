"""Answer validation and hallucination detection service"""
from typing import Dict, Any, List
import re
from app.core.logging import get_logger

logger = get_logger(__name__)


class ValidationService:
    """Validates LLM responses for hallucination and accuracy"""

    def validate_response(
        self,
        response_text: str,
        query: str,
        thread_contexts: List[Dict[str, Any]],
        citations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Comprehensive response validation"""

        validations = {
            "has_answer": self._check_has_answer(response_text),
            "citation_coverage": self._check_citation_coverage(response_text, citations),
            "factual_grounding": self._check_factual_grounding(response_text, thread_contexts),
            "response_relevance": self._check_response_relevance(response_text, query),
            "hallucination_indicators": self._detect_hallucination_indicators(response_text),
            "answer_confidence": self._assess_confidence(response_text)
        }

        # Calculate overall quality score
        quality_score = self._calculate_quality_score(validations)

        logger.info(
            "response_validated",
            quality_score=quality_score,
            has_answer=validations["has_answer"],
            citation_count=len(citations),
            hallucination_flags=len(validations["hallucination_indicators"])
        )

        return {
            "validations": validations,
            "quality_score": quality_score,
            "is_acceptable": quality_score >= 0.6  # Threshold for acceptable response
        }

    def _check_has_answer(self, response_text: str) -> bool:
        """Check if response contains an actual answer"""

        # Common phrases indicating no answer
        no_answer_phrases = [
            "i don't have information",
            "i couldn't find",
            "there is no information",
            "not found in",
            "unable to locate",
            "no relevant information",
            "cannot answer",
            "don't see any"
        ]

        response_lower = response_text.lower()

        for phrase in no_answer_phrases:
            if phrase in response_lower:
                return False

        # Check if response is too short (< 50 chars likely insufficient)
        if len(response_text.strip()) < 50:
            return False

        return True

    def _check_citation_coverage(
        self,
        response_text: str,
        citations: List[Dict[str, Any]]
    ) -> float:
        """Calculate percentage of factual claims that are cited"""

        # Count sentences (rough proxy for claims)
        sentences = re.split(r'[.!?]+', response_text)
        factual_sentences = [
            s for s in sentences
            if len(s.strip()) > 10 and not s.strip().startswith(('However', 'Note', 'Additionally'))
        ]

        if not factual_sentences:
            return 1.0

        # Count sentences with citations
        cited_count = 0
        for sentence in factual_sentences:
            # Check if sentence contains a citation reference [1], [2], etc.
            if re.search(r'\[\d+\]', sentence):
                cited_count += 1

        coverage = cited_count / len(factual_sentences) if factual_sentences else 0.0

        return coverage

    def _check_factual_grounding(
        self,
        response_text: str,
        thread_contexts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check if response claims are grounded in provided context"""

        # Extract key entities and facts from response
        response_entities = self._extract_entities(response_text)

        # Extract entities from context
        context_entities = set()
        for thread_ctx in thread_contexts:
            for message in thread_ctx.get("messages", []):
                if not message.get("is_summary"):
                    text = message.get("text", "")
                    context_entities.update(self._extract_entities(text))

        # Check overlap
        grounded_entities = response_entities & context_entities
        ungrounded_entities = response_entities - context_entities

        grounding_score = len(grounded_entities) / len(response_entities) if response_entities else 1.0

        return {
            "grounding_score": grounding_score,
            "grounded_count": len(grounded_entities),
            "ungrounded_count": len(ungrounded_entities),
            "ungrounded_entities": list(ungrounded_entities)[:5]  # Sample
        }

    def _check_response_relevance(self, response_text: str, query: str) -> float:
        """Check if response is relevant to the query"""

        # Extract key terms from query
        query_terms = set(re.findall(r'\b\w+\b', query.lower()))

        # Remove stop words
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'who', 'when', 'where', 'how', 'why', 'did', 'do', 'does'}
        query_terms = query_terms - stop_words

        if not query_terms:
            return 1.0

        # Check how many query terms appear in response
        response_lower = response_text.lower()
        matched_terms = sum(1 for term in query_terms if term in response_lower)

        relevance_score = matched_terms / len(query_terms)

        return relevance_score

    def _detect_hallucination_indicators(self, response_text: str) -> List[str]:
        """Detect common hallucination patterns"""

        indicators = []

        # Check for overly specific details without citations
        if re.search(r'\d{1,2}:\d{2}(am|pm)', response_text, re.IGNORECASE):
            if not re.search(r'\[\d+\]', response_text):
                indicators.append("Specific time mentioned without citation")

        # Check for definitive statements without citations
        definitive_patterns = [
            r'\bdefin itely\b',
            r'\bcertainly\b',
            r'\bwithout (a )?doubt\b',
            r'\balways\b',
            r'\bnever\b',
            r'\beveryone (agrees|thinks)\b'
        ]

        for pattern in definitive_patterns:
            if re.search(pattern, response_text, re.IGNORECASE):
                # Check if nearby text has citation
                matches = list(re.finditer(pattern, response_text, re.IGNORECASE))
                for match in matches:
                    start = max(0, match.start() - 100)
                    end = min(len(response_text), match.end() + 100)
                    context = response_text[start:end]

                    if not re.search(r'\[\d+\]', context):
                        indicators.append(f"Definitive statement without citation: '{match.group()}'")
                        break

        # Check for vague quantifiers
        vague_patterns = [
            r'\bmany\b',
            r'\bsome\b',
            r'\bseveral\b',
            r'\ba few\b',
            r'\bmost\b'
        ]

        vague_count = sum(len(re.findall(pattern, response_text, re.IGNORECASE)) for pattern in vague_patterns)

        if vague_count > 3:
            indicators.append(f"Excessive vague quantifiers (count: {vague_count})")

        return indicators

    def _assess_confidence(self, response_text: str) -> Dict[str, Any]:
        """Assess the confidence level expressed in the response"""

        confidence_indicators = {
            "high": [r'\b(definitely|certainly|clearly|confirmed)\b'],
            "medium": [r'\b(likely|probably|appears|seems)\b'],
            "low": [r'\b(unclear|uncertain|possibly|might|may)\b'],
            "hedging": [r'\b(however|but|although|while)\b']
        }

        counts = {}
        for level, patterns in confidence_indicators.items():
            count = sum(len(re.findall(pattern, response_text, re.IGNORECASE)) for pattern in patterns)
            counts[level] = count

        # Determine overall confidence
        if counts["low"] > counts["high"]:
            confidence_level = "low"
        elif counts["high"] > counts["low"] + counts["medium"]:
            confidence_level = "high"
        else:
            confidence_level = "medium"

        return {
            "confidence_level": confidence_level,
            "indicators": counts
        }

    def _extract_entities(self, text: str) -> set:
        """Extract simple entities from text (capitalized words, numbers, etc.)"""

        entities = set()

        # Capitalized words (potential entities)
        capitalized = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        entities.update(capitalized)

        # Numbers
        numbers = re.findall(r'\b\d+(?:\.\d+)?\b', text)
        entities.update(numbers)

        # Quoted phrases
        quoted = re.findall(r'"([^"]+)"', text)
        entities.update(quoted)

        return entities

    def _calculate_quality_score(self, validations: Dict[str, Any]) -> float:
        """Calculate overall quality score from validations"""

        score = 0.0

        # Has answer (30%)
        if validations["has_answer"]:
            score += 0.3

        # Citation coverage (25%)
        score += validations["citation_coverage"] * 0.25

        # Factual grounding (25%)
        grounding = validations["factual_grounding"]
        score += grounding["grounding_score"] * 0.25

        # Response relevance (15%)
        score += validations["response_relevance"] * 0.15

        # Hallucination penalty (5%)
        hallucination_count = len(validations["hallucination_indicators"])
        hallucination_penalty = min(hallucination_count * 0.02, 0.05)
        score -= hallucination_penalty

        # Ensure score is between 0 and 1
        score = max(0.0, min(1.0, score))

        return round(score, 3)


# Global validation service instance
validation_service = ValidationService()
