"""Code detection service for extracting code blocks from messages"""
from typing import List, Dict, Optional
import re
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CodeDetector:
    """Enhanced code detection with language identification"""

    # Common programming language keywords for heuristic detection
    # Order matters - more specific patterns should come first
    LANGUAGE_PATTERNS = {
        "java": [
            r'\bimport\s+java\.',  # Java-specific imports (checked first)
            r'\bpublic\s+(class|interface|enum)',
            r'\bprivate\s+\w+',
            r'\bprotected\s+\w+',
            r'\bSystem\.out\.println',
            r'\bextends\s+\w+',
            r'\bimplements\s+\w+',
            r'\bBigDecimal\b',
            r'\bObjects\b',
            r'\bString\[\]',
            r'@Override\b'
        ],
        "python": [
            r'\bdef\s+\w+\s*\(',
            r'\bclass\s+\w+\s*[\(:]',
            r'\bimport\s+\w+',
            r'\bfrom\s+\w+\s+import',
            r'\basync\s+def\b',
            r'\b__init__\b'
        ],
        "javascript": [
            r'\bfunction\s+\w+\s*\(',
            r'\bconst\s+\w+\s*=',
            r'\blet\s+\w+\s*=',
            r'\bvar\s+\w+\s*=',
            r'=>',
            r'\bconsole\.log\b'
        ],
        "typescript": [
            r'\binterface\s+\w+\s*\{',
            r'\btype\s+\w+\s*=',
            r':\s*(string|number|boolean)',
            r'\bexport\s+(interface|type|class)'
        ],
        "go": [
            r'\bfunc\s+\w+\s*\(',
            r'\bpackage\s+\w+',
            r'\btype\s+\w+\s+struct',
            r'\bimport\s*\(',
            r':=\s*'
        ],
        "rust": [
            r'\bfn\s+\w+\s*\(',
            r'\bstruct\s+\w+',
            r'\bimpl\s+\w+',
            r'\buse\s+\w+',
            r'\bpub\s+fn\b'
        ],
        "sql": [
            r'\bSELECT\b',
            r'\bFROM\b',
            r'\bWHERE\b',
            r'\bJOIN\b',
            r'\bCREATE\s+TABLE\b'
        ]
    }

    def detect_code_blocks(self, text: str) -> List[Dict]:
        """
        Extract code blocks from Slack messages

        Returns:
            List of dicts containing code blocks with metadata
        """
        if not text:
            return []

        code_blocks = []

        # Strategy 1: Triple backtick fenced code blocks (```language)
        code_blocks.extend(self._extract_fenced_blocks(text))

        # Strategy 2: Single backtick inline code (`code`)
        if not code_blocks:  # Only extract inline if no fenced blocks found
            code_blocks.extend(self._extract_inline_code(text))

        # Strategy 3: Slack <pre> or <code> tags
        code_blocks.extend(self._extract_html_code_blocks(text))

        # Strategy 4: Raw unfenced code (multi-line code with no fences)
        # Only attempt if no other strategies found code
        if not code_blocks:
            code_blocks.extend(self._extract_raw_code_blocks(text))

        # Deduplicate based on code content
        seen_code = set()
        unique_blocks = []
        for block in code_blocks:
            code = block["code"].strip()
            if code and code not in seen_code:
                seen_code.add(code)
                unique_blocks.append(block)

        logger.info(
            "code_blocks_detected",
            total_blocks=len(unique_blocks),
            languages=[b.get("language") for b in unique_blocks]
        )

        return unique_blocks

    def _extract_fenced_blocks(self, text: str) -> List[Dict]:
        """Extract triple backtick fenced code blocks"""
        blocks = []

        # Pattern: ```language (optional newline) code```
        # More flexible to handle various formats
        pattern = r'```(\w*)\s*(.*?)```'
        matches = re.finditer(pattern, text, re.DOTALL)

        for match in matches:
            language_hint = match.group(1).lower() if match.group(1) else None
            code = match.group(2)

            if not code.strip():
                continue

            # Identify language
            language = self.identify_language(code, language_hint)

            blocks.append({
                "code": code,
                "language": language,
                "start_index": match.start(),
                "end_index": match.end(),
                "is_inline": False,
                "fence_type": "triple_backtick",
                "language_hint": language_hint
            })

        return blocks

    def _extract_inline_code(self, text: str) -> List[Dict]:
        """Extract single backtick inline code"""
        blocks = []

        # Pattern: `code` (single backticks)
        pattern = r'`([^`\n]+)`'
        matches = re.finditer(pattern, text)

        for match in matches:
            code = match.group(1).strip()

            # Filter out very short snippets (likely not code)
            if len(code) < 5:
                continue

            # Check if it looks like code (has programming patterns)
            if not self._looks_like_code(code):
                continue

            language = self.identify_language(code, None)

            blocks.append({
                "code": code,
                "language": language,
                "start_index": match.start(),
                "end_index": match.end(),
                "is_inline": True,
                "fence_type": "single_backtick",
                "language_hint": None
            })

        return blocks

    def _extract_html_code_blocks(self, text: str) -> List[Dict]:
        """Extract Slack <pre> or <code> tags"""
        blocks = []

        # Pattern: <pre>code</pre> or <code>code</code>
        patterns = [
            (r'<pre>(.*?)</pre>', 'pre_tag'),
            (r'<code>(.*?)</code>', 'code_tag')
        ]

        for pattern, tag_type in patterns:
            matches = re.finditer(pattern, text, re.DOTALL)

            for match in matches:
                code = match.group(1).strip()

                if not code or len(code) < 5:
                    continue

                language = self.identify_language(code, None)

                blocks.append({
                    "code": code,
                    "language": language,
                    "start_index": match.start(),
                    "end_index": match.end(),
                    "is_inline": tag_type == 'code_tag',
                    "fence_type": tag_type,
                    "language_hint": None
                })

        return blocks

    def _extract_raw_code_blocks(self, text: str) -> List[Dict]:
        """
        Extract raw unfenced code blocks using heuristics

        Detects code that's pasted without any formatting/fences.
        Uses strong heuristics to avoid false positives:
        - Must have multiple lines (5+)
        - Must have strong code indicators (imports, classes, functions)
        - Must have code-like structure (indentation, semicolons, brackets)
        """
        blocks = []
        lines = text.split('\n')

        # Need at least 5 lines for raw code detection
        if len(lines) < 5:
            return blocks

        # Strategy 1: Try to detect entire message as code
        # Check if most lines look like code
        code_line_count = 0
        total_non_empty_lines = 0

        for line in lines:
            if line.strip():  # Non-empty line
                total_non_empty_lines += 1
                score = self._score_line_as_code(line)
                if score > 0.3:
                    code_line_count += 1

        # If >60% of non-empty lines are code, treat entire message as code
        if total_non_empty_lines > 0:
            code_ratio = code_line_count / total_non_empty_lines
            if code_ratio >= 0.6:
                # Try to identify language
                language = self.identify_language(text, None)

                # Only treat as code if we can identify the language
                if language != "unknown":
                    # Find first code line and last code line to trim non-code prefix/suffix
                    first_code_line = None
                    last_code_line = None

                    for i, line in enumerate(lines):
                        if self._score_line_as_code(line) > 0.3:
                            if first_code_line is None:
                                first_code_line = i
                            last_code_line = i

                    # Extract code from first to last code line
                    if first_code_line is not None and last_code_line is not None:
                        code_lines = lines[first_code_line:last_code_line + 1]
                        code = '\n'.join(code_lines)

                        blocks.append({
                            "code": code,
                            "language": language,
                            "start_index": 0,
                            "end_index": len(text),
                            "is_inline": False,
                            "fence_type": "raw_unfenced",
                            "language_hint": None
                        })

                        return blocks  # Return early if we found code

        # Strategy 2: If entire message isn't code, look for contiguous blocks
        MIN_BLOCK_LINES = 5
        MIN_AVG_SCORE = 0.4

        current_block_start = None
        current_block_lines = []
        current_block_scores = []

        for i, line in enumerate(lines):
            score = self._score_line_as_code(line)

            if score > 0.3 or (current_block_start is not None and line.strip() == ''):
                # Line is code, or blank line within a code block
                if current_block_start is None:
                    current_block_start = i
                current_block_lines.append(line)
                current_block_scores.append(score if score > 0 else 0.1)
            else:
                # Non-code line - end current block
                if current_block_start is not None:
                    if len(current_block_lines) >= MIN_BLOCK_LINES:
                        avg_score = sum(current_block_scores) / len(current_block_scores)
                        if avg_score >= MIN_AVG_SCORE:
                            code = '\n'.join(current_block_lines)
                            language = self.identify_language(code, None)

                            if language != "unknown":
                                blocks.append({
                                    "code": code,
                                    "language": language,
                                    "start_index": 0,
                                    "end_index": len(text),
                                    "is_inline": False,
                                    "fence_type": "raw_unfenced",
                                    "language_hint": None
                                })

                    # Reset
                    current_block_start = None
                    current_block_lines = []
                    current_block_scores = []

        # Check final block
        if current_block_start is not None and len(current_block_lines) >= MIN_BLOCK_LINES:
            avg_score = sum(current_block_scores) / len(current_block_scores)
            if avg_score >= MIN_AVG_SCORE:
                code = '\n'.join(current_block_lines)
                language = self.identify_language(code, None)

                if language != "unknown":
                    blocks.append({
                        "code": code,
                        "language": language,
                        "start_index": 0,
                        "end_index": len(text),
                        "is_inline": False,
                        "fence_type": "raw_unfenced",
                        "language_hint": None
                    })

        return blocks

    def _score_line_as_code(self, line: str) -> float:
        """
        Score a line for how likely it is to be code (0.0 to 1.0)

        Higher scores indicate stronger code signals.
        """
        if not line.strip():
            return 0.0

        score = 0.0

        # Strong indicators (high weight)
        strong_patterns = [
            (r'^\s*(public|private|protected|static|final|const|let|var)\s+', 0.8),
            (r'^\s*(import|from|package|use|require|include)\s+', 0.9),
            (r'^\s*(class|interface|struct|enum|trait)\s+\w+', 0.9),
            (r'^\s*(def|function|func|fn|void|int|String|boolean)\s+\w+\s*\(', 0.8),
            (r'^\s*@\w+', 0.7),  # Annotations/decorators
            (r'^\s*\w+\s*\([^)]*\)\s*\{', 0.7),  # Function call with block
            (r'^\s*(if|while|for|switch|try|catch)\s*\(', 0.6),
        ]

        for pattern, weight in strong_patterns:
            if re.search(pattern, line):
                score = max(score, weight)

        # Medium indicators
        medium_patterns = [
            (r'[{};]$', 0.5),  # Ends with code punctuation
            (r'^\s+\w+\s*=', 0.4),  # Indented assignment
            (r'->', 0.4),  # Arrow operator
            (r'\w+\.\w+\(', 0.5),  # Method call
            (r'/\*\*|\*/|//|#', 0.3),  # Comments
        ]

        for pattern, weight in medium_patterns:
            if re.search(pattern, line):
                score = max(score, weight)

        # Check indentation (code is usually indented)
        if line.startswith('    ') or line.startswith('\t'):
            score += 0.2

        return min(score, 1.0)  # Cap at 1.0

    def identify_language(self, code: str, hint: Optional[str] = None) -> str:
        """
        Identify programming language from code

        Uses:
        1. Language hint (from fence: ```python)
        2. Pattern matching for common language keywords
        3. Fallback to "unknown"
        """
        # Use hint if provided and valid
        if hint and hint in settings.supported_code_languages:
            return hint

        # Try pattern matching
        for language, patterns in self.LANGUAGE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    logger.debug(
                        "language_detected",
                        language=language,
                        pattern=pattern[:50]
                    )
                    return language

        # Fallback
        return "unknown"

    def _looks_like_code(self, text: str) -> bool:
        """
        Heuristic check if text looks like code

        Checks for:
        - Programming operators: =, ==, !=, +=, ->, =>
        - Brackets/parentheses: (), [], {}
        - Common programming symbols: ; : . ->
        - CLI commands: npm, docker, git, ngrok, curl, etc.
        - Shell patterns: flags (--flag, -f), pipes (|), redirects (>)
        """
        # Common CLI tools and commands
        cli_commands = [
            r'\b(npm|yarn|pnpm)\s+(install|run|start|build|test)',
            r'\b(docker|kubectl|helm)\s+\w+',
            r'\b(git)\s+(clone|pull|push|commit|checkout|add|status)',
            r'\b(ngrok)\s+(http|tcp|tls)',
            r'\b(curl|wget)\s+',
            r'\b(pip|poetry|pipenv)\s+(install|uninstall)',
            r'\b(python|node|go|rust|java)\s+',
            r'\b(aws|gcloud|az)\s+',
            r'\b(mysql|psql|redis-cli)\s+',
            r'\b(uvicorn|gunicorn|flask|fastapi)\s+',
        ]

        # Shell/CLI patterns
        shell_patterns = [
            r'--[\w-]+',  # Long flags: --flag-name
            r'\s-[a-zA-Z](\s|$)',  # Short flags: -f
            r'\|\s*\w+',  # Pipes: | grep
            r'>\s*\w+',  # Redirects: > file.txt
            r'&&|\|\|',  # Command chaining
        ]

        # Programming patterns
        code_indicators = [
            r'[=!<>]=',  # ==, !=, <=, >=
            r'[+\-*/%]=',  # +=, -=, etc.
            r'=>|->',  # Arrow operators
            r'\w+\s*\(',  # Function calls
            r'\[.*\]',  # Array access
            r'\{.*\}',  # Object/dict literals
            r';$',  # Statement ending
            r'^\s*\w+:',  # Key-value pairs
        ]

        # Check all pattern types
        all_patterns = cli_commands + shell_patterns + code_indicators

        for pattern in all_patterns:
            if re.search(pattern, text):
                return True

        return False


# Global instance
code_detector = CodeDetector()
