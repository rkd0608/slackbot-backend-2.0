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
                    except:
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

Important:
- Carefully review ALL messages in the provided context before stating information is not available
- When code or technical content is shared in messages, include it in your response
- If information genuinely isn't in the context, then state that clearly
- Provide outdated information when more recent context exists is not preferred"""

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
- Include complete, runnable code snippets
- Preserve code formatting and syntax
- Add brief explanations of what the code does
- Cite the author and context: [Channel, @User, timestamp]
- If multiple implementations exist, show the most relevant or recent one
- Include any important warnings or caveats mentioned in discussions"""

    def _get_summary_prompt(self) -> str:
        """Prompt for summary queries"""
        return """For this SUMMARY query:
- Provide a concise overview of the main points
- Organize by themes or chronologically (whichever is more appropriate)
- Highlight key decisions, action items, and outcomes
- Use bullet points for readability
- Include citations for major points
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
- Number the steps clearly
- Include any prerequisites or setup needed
- Add code examples or commands where relevant
- Cite the source of the guidance
- Include common pitfalls or troubleshooting tips if mentioned
- Link to any related documentation mentioned"""


# Global prompt service instance
prompt_service = PromptService()
