"""
Team Vocabulary Learning Service

Automatically learns and applies company-specific vocabulary:
- Abbreviations: "k8s" -> "Kubernetes", "FE" -> "frontend"
- Product names: "portal" -> "customer portal"
- Internal jargon: "ship it" -> "deploy to production"
- Team-specific terms learned from usage patterns

Learning strategies:
1. Co-occurrence analysis: Terms used together frequently
2. Entity expansion: Short terms near long forms
3. User feedback: Learn from query rewrites that get better results
4. Common abbreviations: Seed with known tech abbreviations
"""
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc

from app.core.logging import get_logger
from app.models.team_vocabulary import TeamVocabulary

logger = get_logger(__name__)


# Patterns that indicate invalid vocabulary entries
INVALID_PATTERNS = {
    # Code and syntax patterns
    'code_operators': r'[=<>!]+|&&|\|\||->|=>|::|\+\+|--',
    'code_symbols': r'[\{\}\[\];,\(\)\.]+.*[\{\}\[\];,\(\)\.]+',  # Multiple code punctuation
    'sql_keywords': r'\b(SELECT|WHERE|FROM|JOIN|ORDER BY|GROUP BY|HAVING|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b',
    'programming_syntax': r'(\w+\.)+\w+|(\w+::)+\w+',  # Dotted or namespaced names
    'function_calls': r'\w+\([^)]*\).*\w+\([^)]*\)',  # Multiple function calls
    'boolean_logic': r'==|!=|>=|<=|\.and\(|\.or\(|\.not\(',
    'file_paths': r'[/\\][\w/\\\.]+[/\\]',
    'urls': r'https?://|www\.',
    'email': r'[\w\.-]+@[\w\.-]+',

    # Common words that should never be replaced
    'common_words': r'\b(the|is|are|was|were|be|been|being|have|has|had|do|does|did|will|would|could|should|may|might|must|can|where|when|what|who|why|how|that|this|these|those|i|you|he|she|it|we|they|me|him|her|us|them|my|your|his|her|its|our|their)\b',

    # Too many special characters
    'excessive_special_chars': r'.*[^a-zA-Z0-9\s]{3,}.*',

    # Numeric patterns (IDs, version numbers, etc)
    'numeric_ids': r'\d{3,}|v?\d+\.\d+',
}

# Common SQL/code keywords and English words that should never be vocabulary terms
FORBIDDEN_TERMS = {
    # SQL keywords
    'select', 'where', 'from', 'join', 'order', 'by', 'group',
    'insert', 'update', 'delete', 'create', 'alter', 'drop',
    'table', 'database', 'index', 'view',

    # Programming keywords
    'if', 'else', 'for', 'while', 'return', 'function', 'class',
    'def', 'async', 'await', 'import', 'export', 'const', 'let', 'var',
    'true', 'false', 'null', 'undefined', 'none', 'nil',
    'and', 'or', 'not', 'in', 'is',

    # Common programming terms
    'string', 'int', 'float', 'boolean', 'object', 'array',
    'list', 'dict', 'set', 'tuple', 'map', 'filter', 'reduce',
    'get', 'set', 'put', 'post', 'patch', 'delete',
    'equals', 'override', 'implements', 'extends',
    'public', 'private', 'protected', 'static', 'final',
    'long', 'short', 'byte', 'char', 'double',

    # Very common English words (greetings, transitions, etc.)
    'hi', 'hello', 'hey', 'thanks', 'thank', 'please', 'yes', 'no',
    'good', 'great', 'nice', 'cool', 'awesome', 'perfect',
    'ok', 'okay', 'sure', 'yep', 'nope', 'alright',
    'here', 'there', 'now', 'then', 'also', 'too', 'very',
    'just', 'only', 'also', 'even', 'still', 'already',
    'more', 'less', 'most', 'least', 'few', 'many', 'much',
    'some', 'any', 'all', 'both', 'each', 'every',
    'before', 'after', 'during', 'until', 'since', 'while',
    'right', 'left', 'up', 'down', 'over', 'under',
    'new', 'old', 'first', 'last', 'next', 'previous',
    'big', 'small', 'large', 'huge', 'tiny',
    'exactly', 'definitely', 'probably', 'maybe', 'perhaps',

    # Common workplace words
    'morning', 'afternoon', 'evening', 'night',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'week', 'month', 'year', 'day', 'today', 'tomorrow', 'yesterday',
    'meeting', 'call', 'chat', 'message', 'email',
    'team', 'work', 'project', 'task', 'issue',

    # Generic tech terms (too common to be useful)
    'user', 'users', 'client', 'server', 'app', 'data',
    'file', 'files', 'code', 'test', 'tests',
    'error', 'bug', 'fix', 'update', 'change',
    'event', 'message', 'messages', 'query', 'queries',
    'entity', 'entities', 'context', 'config',
    'cache', 'redis', 'database', 'api', 'endpoint',
    'slack', 'github', 'pinecone',
}


def is_valid_vocabulary_entry(term: str, canonical_form: str) -> Tuple[bool, Optional[str]]:
    """
    Validate if a term-canonical pair is suitable for vocabulary learning.

    Returns:
        Tuple of (is_valid, reason_if_invalid)
    """
    # Check if term is a forbidden keyword
    if term.lower() in FORBIDDEN_TERMS:
        return False, f"Term '{term}' is a reserved keyword"

    # Check if canonical form contains invalid patterns
    for pattern_name, pattern in INVALID_PATTERNS.items():
        if re.search(pattern, canonical_form, re.IGNORECASE):
            return False, f"Canonical form contains {pattern_name}: {pattern}"

    # Check if canonical form is too long (likely a sentence/code snippet)
    if len(canonical_form) > 100:
        return False, "Canonical form too long (>100 chars)"

    # Check if canonical form has too many words (likely a sentence)
    word_count = len(canonical_form.split())
    if word_count > 5:
        return False, f"Canonical form has too many words ({word_count} > 5)"

    # Check for excessive special characters
    special_char_count = len(re.findall(r'[^a-zA-Z0-9\s-]', canonical_form))
    if special_char_count > 2:
        return False, f"Too many special characters ({special_char_count})"

    # Term should be reasonably short
    if len(term) > 20:
        return False, "Term too long (>20 chars)"

    # Term should contain at least some letters
    if not re.search(r'[a-zA-Z]', term):
        return False, "Term must contain letters"

    # Canonical form should be longer than term (for abbreviations)
    # Or equal (for product names)
    if len(canonical_form) < len(term) - 2:  # Allow small variance
        return False, "Canonical form should not be shorter than term"

    return True, None


# Common tech abbreviations to seed vocabulary
COMMON_ABBREVIATIONS = {
    "k8s": "kubernetes",
    "api": "application programming interface",
    "db": "database",
    "fe": "frontend",
    "be": "backend",
    "ui": "user interface",
    "ux": "user experience",
    "ci": "continuous integration",
    "cd": "continuous deployment",
    "qa": "quality assurance",
    "pr": "pull request",
    "mvp": "minimum viable product",
    "poc": "proof of concept",
    "sla": "service level agreement",
    "tbd": "to be determined",
    "wip": "work in progress",
    "asap": "as soon as possible",
}


class TeamVocabularyService:
    """
    Learns and applies team-specific vocabulary

    Usage:
        service = TeamVocabularyService(db)

        # Learn from message
        await service.learn_from_message(
            team_id="T123",
            message="k8s deployment failed in prod"
        )

        # Expand query
        expanded = await service.expand_query(
            team_id="T123",
            query="k8s prod issues"
        )
        # Returns: "kubernetes production issues"
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def seed_common_abbreviations(self, team_id: str) -> int:
        """
        Seed vocabulary with common tech abbreviations

        Args:
            team_id: Workspace team ID

        Returns:
            Number of terms seeded
        """
        try:
            seeded_count = 0

            for abbr, canonical in COMMON_ABBREVIATIONS.items():
                # Check if already exists
                result = await self.db.execute(
                    select(TeamVocabulary).where(
                        and_(
                            TeamVocabulary.team_id == team_id,
                            TeamVocabulary.term == abbr.lower()
                        )
                    )
                )
                existing = result.scalar_one_or_none()

                if not existing:
                    vocab = TeamVocabulary(
                        team_id=team_id,
                        term=abbr.lower(),
                        canonical_form=canonical,
                        term_type="abbreviation",
                        confidence_score=0.8,  # High confidence for common terms
                        learned_from="common_abbreviations",
                        occurrence_count=1
                    )
                    self.db.add(vocab)
                    seeded_count += 1

            await self.db.commit()

            logger.info(
                "common_abbreviations_seeded",
                team_id=team_id,
                seeded_count=seeded_count
            )

            return seeded_count

        except Exception as e:
            logger.error("seed_common_abbreviations_error", error=str(e))
            return 0

    async def learn_from_message(
        self,
        team_id: str,
        message: str,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Learn vocabulary from a message

        Strategies:
        1. Detect abbreviation patterns (e.g., "k8s (Kubernetes)")
        2. Detect short-long pairs (e.g., "FE code" near "frontend")
        3. Detect capitalized product names

        Args:
            team_id: Workspace team ID
            message: Message text
            user_id: Optional user ID for context

        Returns:
            List of learned terms
        """
        try:
            learned_terms = []

            # Strategy 1: Explicit abbreviation patterns
            # Pattern: "abbr (full form)" or "full form (abbr)"
            explicit_patterns = [
                r'(\w+)\s*\(([^)]+)\)',  # abbr (full)
                r'\((\w+)\)\s*([^(\n]+)',  # (abbr) full
            ]

            for pattern in explicit_patterns:
                matches = re.finditer(pattern, message, re.IGNORECASE)
                for match in matches:
                    short, long = match.group(1).strip(), match.group(2).strip()

                    # Determine which is abbreviation (shorter one)
                    if len(short) < len(long) and len(short) <= 10:
                        learned = await self._record_term_mapping(
                            team_id=team_id,
                            term=short.lower(),
                            canonical_form=long.lower(),
                            term_type="abbreviation",
                            learned_from="explicit_pattern",
                            context_example=message[:200]
                        )
                        if learned:
                            learned_terms.append(learned)

            # Strategy 2: Capitalized product names
            # Look for consistently capitalized words (potential product names)
            capitalized_words = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)*\b', message)
            for word in capitalized_words:
                if len(word) > 3 and word not in ['The', 'And', 'But', 'For']:
                    # This might be a product name
                    await self._record_potential_product_name(
                        team_id=team_id,
                        term=word.lower(),
                        context_example=message[:200]
                    )

            logger.info(
                "learned_from_message",
                team_id=team_id,
                learned_count=len(learned_terms)
            )

            return learned_terms

        except Exception as e:
            logger.error("learn_from_message_error", error=str(e))
            return []

    async def learn_from_cooccurrence(
        self,
        team_id: str,
        short_term: str,
        long_term: str,
        cooccurrence_count: int = 1
    ) -> bool:
        """
        Learn mapping from co-occurrence of short and long terms

        Args:
            team_id: Workspace team ID
            short_term: Potential abbreviation
            long_term: Potential canonical form
            cooccurrence_count: How many times they appeared together

        Returns:
            True if learned
        """
        try:
            # Validate this is likely a good mapping
            if len(short_term) >= len(long_term):
                return False

            if len(short_term) > 10:  # Too long to be an abbreviation
                return False

            # Check if abbreviation matches canonical form
            # e.g., "k8s" matches "kubernetes" (k + 8 letters + s)
            # or "db" matches "database" (first letters)
            confidence = self._calculate_abbreviation_confidence(short_term, long_term)

            if confidence < 0.3:  # Too low confidence
                return False

            return await self._record_term_mapping(
                team_id=team_id,
                term=short_term.lower(),
                canonical_form=long_term.lower(),
                term_type="abbreviation",
                learned_from="cooccurrence",
                confidence_score=confidence,
                occurrence_count=cooccurrence_count
            )

        except Exception as e:
            logger.error("learn_from_cooccurrence_error", error=str(e))
            return False

    async def expand_query(
        self,
        team_id: str,
        query: str,
        min_confidence: float = 0.5
    ) -> Dict[str, Any]:
        """
        Expand query with learned vocabulary

        Args:
            team_id: Workspace team ID
            query: Original query
            min_confidence: Minimum confidence score to apply

        Returns:
            Dict with:
            - original_query: Original query
            - expanded_query: Query with abbreviations expanded
            - expansions: List of expansions applied
        """
        try:
            # Get active vocabulary for team
            result = await self.db.execute(
                select(TeamVocabulary).where(
                    and_(
                        TeamVocabulary.team_id == team_id,
                        TeamVocabulary.is_active == "true",
                        TeamVocabulary.confidence_score >= min_confidence
                    )
                ).order_by(desc(TeamVocabulary.confidence_score))
            )
            vocab_items = result.scalars().all()

            if not vocab_items:
                return {
                    "original_query": query,
                    "expanded_query": query,
                    "expansions": []
                }

            # Apply expansions
            expanded_query = query
            expansions = []

            for vocab in vocab_items:
                # Validate this entry before applying (safety check)
                is_valid, reason = is_valid_vocabulary_entry(vocab.term, vocab.canonical_form)
                if not is_valid:
                    logger.warning(
                        "invalid_vocabulary_skipped_during_expansion",
                        team_id=team_id,
                        term=vocab.term,
                        canonical_form=vocab.canonical_form,
                        reason=reason
                    )
                    # Automatically disable invalid entry
                    vocab.is_active = "false"
                    continue

                # Use word boundaries to match whole words only
                pattern = r'\b' + re.escape(vocab.term) + r'\b'
                if re.search(pattern, expanded_query, re.IGNORECASE):
                    # Replace term with canonical form
                    expanded_query = re.sub(
                        pattern,
                        vocab.canonical_form,
                        expanded_query,
                        flags=re.IGNORECASE
                    )

                    expansions.append({
                        "term": vocab.term,
                        "canonical_form": vocab.canonical_form,
                        "confidence": vocab.confidence_score
                    })

                    # Update usage count
                    vocab.occurrence_count += 1
                    vocab.last_seen = datetime.utcnow()

            if expansions:
                await self.db.commit()

                logger.info(
                    "query_expanded_with_vocabulary",
                    team_id=team_id,
                    original=query,
                    expanded=expanded_query,
                    expansions_count=len(expansions)
                )

            return {
                "original_query": query,
                "expanded_query": expanded_query,
                "expansions": expansions
            }

        except Exception as e:
            logger.error("expand_query_error", error=str(e))
            return {
                "original_query": query,
                "expanded_query": query,
                "expansions": [],
                "error": str(e)
            }

    async def get_team_vocabulary(
        self,
        team_id: str,
        limit: int = 100,
        min_confidence: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Get team's learned vocabulary

        Args:
            team_id: Workspace team ID
            limit: Maximum number of terms to return
            min_confidence: Minimum confidence score

        Returns:
            List of vocabulary terms
        """
        try:
            result = await self.db.execute(
                select(TeamVocabulary).where(
                    and_(
                        TeamVocabulary.team_id == team_id,
                        TeamVocabulary.is_active == "true",
                        TeamVocabulary.confidence_score >= min_confidence
                    )
                ).order_by(
                    desc(TeamVocabulary.occurrence_count),
                    desc(TeamVocabulary.confidence_score)
                ).limit(limit)
            )
            vocab_items = result.scalars().all()

            return [item.to_dict() for item in vocab_items]

        except Exception as e:
            logger.error("get_team_vocabulary_error", error=str(e))
            return []

    async def _record_term_mapping(
        self,
        team_id: str,
        term: str,
        canonical_form: str,
        term_type: str,
        learned_from: str,
        confidence_score: float = 0.5,
        occurrence_count: int = 1,
        context_example: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Record or update term mapping"""
        try:
            # Validate the vocabulary entry before recording
            is_valid, reason = is_valid_vocabulary_entry(term, canonical_form)
            if not is_valid:
                logger.warning(
                    "invalid_vocabulary_entry_rejected",
                    team_id=team_id,
                    term=term,
                    canonical_form=canonical_form,
                    reason=reason
                )
                return None

            # Check if term already exists
            result = await self.db.execute(
                select(TeamVocabulary).where(
                    and_(
                        TeamVocabulary.team_id == team_id,
                        TeamVocabulary.term == term
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing
                existing.occurrence_count += occurrence_count
                existing.last_seen = datetime.utcnow()

                # Increase confidence if same canonical form
                if existing.canonical_form == canonical_form:
                    existing.confidence_score = min(
                        1.0,
                        existing.confidence_score + 0.1
                    )

                # Add context example if provided
                if context_example:
                    examples = existing.context_examples or []
                    examples.append({
                        "text": context_example,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    existing.context_examples = examples[-10:]  # Keep last 10

            else:
                # Create new
                vocab = TeamVocabulary(
                    team_id=team_id,
                    term=term,
                    canonical_form=canonical_form,
                    term_type=term_type,
                    learned_from=learned_from,
                    confidence_score=confidence_score,
                    occurrence_count=occurrence_count,
                    context_examples=[{
                        "text": context_example,
                        "timestamp": datetime.utcnow().isoformat()
                    }] if context_example else []
                )
                self.db.add(vocab)
                existing = vocab

            await self.db.commit()

            logger.info(
                "term_mapping_recorded",
                team_id=team_id,
                term=term,
                canonical_form=canonical_form,
                confidence=confidence_score
            )

            return existing.to_dict()

        except Exception as e:
            logger.error("record_term_mapping_error", error=str(e))
            return None

    async def _record_potential_product_name(
        self,
        team_id: str,
        term: str,
        context_example: str
    ) -> bool:
        """Record a potential product name (low confidence initially)"""
        return await self._record_term_mapping(
            team_id=team_id,
            term=term,
            canonical_form=term,  # Product names don't expand
            term_type="product_name",
            learned_from="capitalization_pattern",
            confidence_score=0.3,  # Start with low confidence
            context_example=context_example
        ) is not None

    def _calculate_abbreviation_confidence(
        self,
        short_term: str,
        long_term: str
    ) -> float:
        """
        Calculate confidence that short_term is abbreviation of long_term

        Scoring:
        - Initial letters match: +0.4
        - Numeronym (e.g., k8s): +0.3
        - Length ratio: +0.2
        - Contains all unique letters: +0.1
        """
        short = short_term.lower()
        long = long_term.lower()

        score = 0.0

        # Check if initial letters match
        if long.startswith(short[0]):
            score += 0.4

        # Check for numeronym pattern (e.g., k8s = k + 8 letters + s)
        if re.match(r'[a-z]\d+[a-z]', short):
            num_match = re.search(r'\d+', short)
            if num_match:
                num = int(num_match.group())
                expected_length = num + 2
                if abs(len(long) - expected_length) <= 2:
                    score += 0.3

        # Length ratio
        if len(short) <= len(long) / 3:
            score += 0.2

        # All letters in short appear in long
        if all(c in long for c in short if c.isalpha()):
            score += 0.1

        return min(1.0, score)

    async def disable_term(
        self,
        team_id: str,
        term: str
    ) -> bool:
        """
        Disable a term mapping (e.g., if it's incorrect)

        Args:
            team_id: Workspace team ID
            term: Term to disable

        Returns:
            True if disabled
        """
        try:
            result = await self.db.execute(
                select(TeamVocabulary).where(
                    and_(
                        TeamVocabulary.team_id == team_id,
                        TeamVocabulary.term == term
                    )
                )
            )
            vocab = result.scalar_one_or_none()

            if vocab:
                vocab.is_active = "false"
                await self.db.commit()

                logger.info("term_disabled", team_id=team_id, term=term)
                return True

            return False

        except Exception as e:
            logger.error("disable_term_error", error=str(e))
            return False


def get_team_vocabulary_service(db: AsyncSession) -> TeamVocabularyService:
    """Get team vocabulary service instance"""
    return TeamVocabularyService(db)
