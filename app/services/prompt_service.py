"""Prompt engineering service for different query intents"""
from typing import Dict, Any, List
from datetime import datetime
from app.core.logging import get_logger

logger = get_logger(__name__)


class PromptService:
    """Generates prompts for LLM based on query intent and context"""

    def __init__(self):
        self.system_prompts = {
            "base": self._get_base_system_prompt(),
            "factual": self._get_factual_prompt(),
            "code": self._get_code_prompt(),
            "summary": self._get_summary_prompt(),
            "timeline": self._get_timeline_prompt(),
            "who": self._get_who_prompt(),
            "comparison": self._get_comparison_prompt(),
            "howto": self._get_howto_prompt()
        }

    def build_prompt(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build complete prompt with system message and user message"""

        # Get primary intent
        intents = query_analysis.get("intents", ["factual"])
        primary_intent = intents[0]

        # Build system prompt
        system_prompt = self._build_system_prompt(primary_intent)

        # Build user message with context
        user_message = self._build_user_message(query, query_analysis, context)

        logger.info(
            "prompt_built",
            intent=primary_intent,
            context_threads=len(context.get("thread_contexts", [])),
            total_messages=context.get("meta_context", {}).get("total_messages", 0)
        )

        return {
            "system": system_prompt,
            "user": user_message,
            "intent": primary_intent
        }

    def _build_system_prompt(self, intent: str) -> str:
        """Build system prompt based on intent"""

        base = self.system_prompts["base"]
        intent_specific = self.system_prompts.get(intent, "")

        return f"{base}\n\n{intent_specific}"

    def _build_user_message(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        context: Dict[str, Any]
    ) -> str:
        """Build user message with formatted context"""

        parts = []

        # Add meta-context
        meta = context.get("meta_context", {})
        if meta:
            parts.append("## Context Overview")
            parts.append(f"- Found {meta.get('total_threads', 0)} relevant discussions")
            parts.append(f"- Total messages: {meta.get('total_messages', 0)}")

            channels = meta.get("channels", [])
            if channels:
                parts.append(f"- Channels: {', '.join(channels)}")

            participants = meta.get("participants", [])
            if participants:
                parts.append(f"- Participants: {', '.join(participants[:10])}")
                if len(participants) > 10:
                    parts.append(f"  (and {len(participants) - 10} more)")

            time_span = meta.get("time_span")
            if time_span:
                parts.append(f"- Time period: {time_span.get('earliest')} to {time_span.get('latest')}")

            parts.append("")

        # Add file results
        file_results = context.get("file_results", [])
        if file_results:
            parts.append("## Relevant Files\n")

            for idx, file_res in enumerate(file_results[:5], 1):  # Limit to top 5 files
                parts.append(f"### File {idx}: {file_res.get('filename', 'unknown')}")
                parts.append(f"**Type**: {file_res.get('filetype', 'unknown')}")

                if file_res.get('title'):
                    parts.append(f"**Title**: {file_res['title']}")

                if file_res.get('uploaded_by_name'):
                    parts.append(f"**Uploaded by**: @{file_res['uploaded_by_name']}")

                if file_res.get('created_at'):
                    try:
                        dt = datetime.fromisoformat(str(file_res['created_at']).replace('Z', '+00:00'))
                        time_str = dt.strftime("%Y-%m-%d %H:%M")
                        parts.append(f"**Uploaded**: {time_str}")
                    except (ValueError, TypeError, AttributeError):
                        pass

                if file_res.get('channel_name'):
                    parts.append(f"**Channel**: #{file_res['channel_name']}")

                # Add download link prominently
                if file_res.get('slack_url'):
                    parts.append(f"**Download**: {file_res['slack_url']}")

                # Add extracted text preview (shorter for files)
                extracted_text = file_res.get('extracted_text', '')
                if extracted_text:
                    preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text
                    parts.append(f"\n**Content Preview**:")
                    parts.append(f"{preview}\n")

                parts.append("")

        # Add thread contexts
        thread_contexts = context.get("thread_contexts", [])
        if thread_contexts:
            parts.append("## Relevant Discussions\n")

            for idx, thread_ctx in enumerate(thread_contexts[:10], 1):  # Limit to top 10
                parts.append(f"### Discussion {idx}")
                parts.append(f"**Channel**: #{thread_ctx.get('channel_name', 'unknown')}")

                if thread_ctx.get("summary"):
                    parts.append(f"**Summary**: {thread_ctx['summary']}")

                messages = thread_ctx.get("messages", [])
                parts.append(f"**Messages** ({len(messages)}):\n")

                for msg in messages:
                    if msg.get("is_summary"):
                        parts.append(f"_{msg['text']}_\n")
                        continue

                    timestamp = msg.get("timestamp", "")
                    user = msg.get("user_name", msg.get("user_id", "Unknown"))
                    text = msg.get("text", "")

                    # Format timestamp
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        time_str = dt.strftime("%Y-%m-%d %H:%M")
                    except (ValueError, TypeError, AttributeError):
                        time_str = timestamp

                    parts.append(f"**{user}** ({time_str}):")
                    parts.append(f"{text}\n")

                    # Add reaction info if significant
                    reactions = msg.get("reactions", [])
                    if reactions:
                        reaction_str = ", ".join([
                            f":{r['name']}: {r['count']}"
                            for r in reactions
                        ])
                        parts.append(f"_Reactions: {reaction_str}_\n")

                parts.append("")

        # Add query
        parts.append("## User Question")
        parts.append(f"{query}\n")

        # Add specific instructions based on query analysis
        parts.append("## Instructions")

        if query_analysis.get("has_code_intent"):
            parts.append("- The user is looking for code. Include complete code snippets with syntax highlighting.")

        temporal = query_analysis.get("temporal")
        if temporal:
            parts.append(f"- Pay special attention to the time period: {temporal.get('phrase', 'specified')}")

        entities = query_analysis.get("entities", [])
        if entities:
            entity_texts = [e["text"] for e in entities]
            parts.append(f"- Focus on information related to: {', '.join(entity_texts)}")

        # Citation instructions
        if file_results:
            parts.append("- When referencing files, provide the download link from the 'Download' field in your answer.")
            parts.append("- Format file references as: 'filename (Download: URL)'")
            parts.append("- For message information, cite with [Channel, @User, timestamp] format.")
        else:
            parts.append("- Provide citations with [Channel, @User, timestamp] format for all factual claims.")

        parts.append("- If information is not found in the context, explicitly state that.")
        parts.append("- Be concise but comprehensive.")

        return "\n".join(parts)

    def _get_base_system_prompt(self) -> str:
        """Base system prompt for all queries"""
        return """You are an AI assistant with complete access to a company's Slack workspace history.

Your role is to help users find information, understand discussions, and get insights from their team's communication.

Core Principles:
1. **Accuracy**: Provide information that is directly supported by the provided context
2. **Citations**: Always cite your sources with [Channel, @User, timestamp] format
3. **Clarity**: Provide clear, well-structured answers
4. **Thoroughness**: Carefully examine all provided messages and discussions before concluding information is absent
5. **Context-Aware**: Consider the temporal, social, and topical context of discussions

Formatting Guidelines (Slack-compatible markdown):
- Use *bold* for emphasis (single asterisks, not double)
- Use _italic_ for secondary emphasis (underscores, not single asterisks)
- Use `code` for inline code (single backticks)
- For multi-line code blocks, ALWAYS use this EXACT format:
  ```language
  code here
  ```
  CRITICAL: Opening ``` must be followed by language (python, javascript, etc), then newline, then code, then closing ``` on its own line
- Use • for bullet points
- Keep paragraphs separated by blank lines
- Use clear section headers when organizing information

CODE BLOCK RULES:
✓ CORRECT:
```python
import requests
response = requests.get(url)
print(response.text)
```

✗ WRONG (missing closing backticks):
```python
import requests
response = requests.get(url)

✗ WRONG (no newline after language):
```python import requests

✗ WRONG (closing backticks not on separate line):
```python
code here```

CRITICAL: NEVER split code into multiple parts (e.g., "Part 1/2", "Part 2/2"). Always provide complete code in ONE continuous block, regardless of length. If code is long, include ALL of it in a single code block.

Important:
- Carefully review ALL messages in the provided context before stating information is not available
- When code or technical content is shared in messages, include it in your response COMPLETELY in one block
- NEVER truncate or split code - always provide the FULL code snippet
- If information genuinely isn't in the context, then state that clearly
- Provide outdated information when more recent context exists is not preferred
- Format your response to be easily readable in Slack"""

    def _get_factual_prompt(self) -> str:
        """Prompt for factual queries"""
        return """For this FACTUAL query:
- Extract specific facts, decisions, and outcomes from discussions
- Cite every claim with [Channel, @User, timestamp]
- If there are conflicting statements, present both with citations
- Highlight the most recent or authoritative information
- Structure the answer as: Direct answer, then supporting details with citations"""

    def _get_code_prompt(self) -> str:
        """Prompt for code queries"""
        return """For this CODE query:
- Include complete, runnable code snippets with PROPER formatting
- CRITICAL: Use proper code block syntax:
  ```language
  code here
  ```
  Opening ``` MUST be followed by language, newline, code, then closing ``` on its own line
- Preserve exact code formatting, indentation, and syntax from the source messages
- NEVER split code into "Part 1/2" and "Part 2/2" - provide ALL code in ONE continuous block
- Add brief explanations OUTSIDE the code blocks
- Cite the author and context: [Channel, @User, timestamp]
- If multiple implementations exist, show the most relevant or recent one
- Include any important warnings or caveats mentioned in discussions
- Test your response mentally: can you locate both opening and closing ```? If not, fix it."""

    def _get_summary_prompt(self) -> str:
        """Prompt for summary queries"""
        return """For this SUMMARY query:
- Provide a concise overview of the main points
- Organize by themes or chronologically (whichever is more appropriate)
- Highlight key decisions, action items, and outcomes
- Use bullet points (•) for better readability
- Include citations for major points
- Use *bold* for key terms and decisions
- End with a brief conclusion or current status"""

    def _get_timeline_prompt(self) -> str:
        """Prompt for timeline queries"""
        return """For this TIMELINE query:
- Create a chronological narrative of events
- Use clear date/time markers
- Show progression and evolution of discussions
- Highlight key milestones, decisions, or turning points
- Cite each event with [Channel, @User, timestamp]
- Format as a timeline with dates and descriptions"""

    def _get_who_prompt(self) -> str:
        """Prompt for attribution queries"""
        return """For this WHO/ATTRIBUTION query:
- Identify the specific person(s) involved
- Provide direct quotes when relevant
- Include context about when and where they said it
- If multiple people contributed, list them all
- Cite with [Channel, @User, timestamp]
- Distinguish between the original author and people who agreed/disagreed"""

    def _get_comparison_prompt(self) -> str:
        """Prompt for comparison queries"""
        return """For this COMPARISON query:
- Structure as a clear comparison (e.g., table or side-by-side format)
- Identify key dimensions of comparison
- Present pros and cons objectively
- Include team sentiment if discussed
- Show any consensus or final decision
- Cite sources for each point of comparison"""

    def _get_howto_prompt(self) -> str:
        """Prompt for how-to queries"""
        return """For this HOW-TO query:
- Provide step-by-step instructions
- Number the steps clearly using *1.*, *2.*, etc.
- Include any prerequisites or setup needed
- Add code examples with PROPERLY FORMATTED code blocks:
  ```language
  code here
  ```
  ENSURE closing ``` exists on its own line
- Use *bold* for important actions or commands
- Cite the source of the guidance
- Include common pitfalls or troubleshooting tips if mentioned
- Link to any related documentation mentioned
- Double-check: every ``` opening MUST have a matching ``` closing"""


# Global prompt service instance
prompt_service = PromptService()
