"""Format responses for Slack Block Kit"""
from typing import List, Dict, Any, Optional
import re
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResponseFormatter:
    """Formats bot responses for Slack using Block Kit"""

    @staticmethod
    def convert_to_slack_markdown(text: str) -> str:
        """Convert standard markdown to Slack mrkdwn format"""

        # Convert markdown headers (### Header) to *Header*
        text = re.sub(r'^###\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
        text = re.sub(r'^##\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
        text = re.sub(r'^#\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

        # Convert **bold** to *bold*
        text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)

        # Convert __bold__ to *bold*
        text = re.sub(r'__(.+?)__', r'*\1*', text)

        # Convert *italic* to _italic_ (but not if already converted bold)
        # This is tricky because * is used for bold in Slack
        # We'll convert single asterisks that aren't part of bold to underscores
        text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', text)

        # Convert `code` to `code` (already compatible)

        # Convert ```code blocks``` to Slack format
        text = re.sub(r'```(\w+)?\n(.+?)\n```', r'```\2```', text, flags=re.DOTALL)

        # Convert [text](url) to <url|text>
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<\2|\1>', text)

        # Convert numbered lists to better formatting
        text = re.sub(r'^(\d+)\.\s+', r'*\1.* ', text, flags=re.MULTILINE)

        # Convert bullet points - fix spacing
        text = re.sub(r'^[•\-]\s+', r'• ', text, flags=re.MULTILINE)

        return text

    @staticmethod
    def format_answer_response(
        answer: str,
        citations: List[Dict[str, Any]],
        confidence: float = None,
        query_id: str = None
    ) -> Dict[str, Any]:
        """Format LLM answer with citations as Slack blocks"""
        blocks = []

        # Convert markdown to Slack format
        formatted_answer = ResponseFormatter.convert_to_slack_markdown(answer)

        # Main answer text
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": formatted_answer
            }
        })

        # Add divider if there are citations
        if citations:
            blocks.append({"type": "divider"})

            # Citations section
            citation_lines = []
            for idx, citation in enumerate(citations[:5], 1):  # Limit to 5 citations
                channel_name = citation.get("channel_name", "unknown")
                channel_id = citation.get("channel_id", "")
                user = citation.get("user_name", "unknown")
                timestamp = citation.get("timestamp", "")
                url = citation.get("url", "")

                # Format channel reference - use ID if available, otherwise name
                if channel_id:
                    channel_ref = f"<#{channel_id}>"
                else:
                    channel_ref = f"#{channel_name}"

                if url:
                    citation_lines.append(
                        f"{idx}. {channel_ref} - @{user} - <{url}|View Message>"
                    )
                else:
                    citation_lines.append(
                        f"{idx}. {channel_ref} - @{user} - _{timestamp}_"
                    )

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Sources:\n" + "\n".join(citation_lines)
                }
            })

        # Add confidence indicator if provided
        if confidence is not None:
            confidence_text = f"Confidence: {confidence:.0%}"
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": confidence_text
                    }
                ]
            })

        # Add feedback buttons if query_id provided
        if query_id:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "actions",
                "block_id": f"feedback_{query_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "👍 Helpful"
                        },
                        "style": "primary",
                        "value": f"{query_id}:thumbs_up",
                        "action_id": "feedback_thumbs_up"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "👎 Not Helpful"
                        },
                        "style": "danger",
                        "value": f"{query_id}:thumbs_down",
                        "action_id": "feedback_thumbs_down"
                    }
                ]
            })

        return {
            "blocks": blocks,
            "text": answer  # Fallback text for notifications
        }

    @staticmethod
    def format_search_results(
        results: List[Dict[str, Any]],
        query: str
    ) -> Dict[str, Any]:
        """Format search results as Slack blocks"""
        blocks = []

        # Header
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Search Results for: {query}\n\nFound {len(results)} relevant messages:"
            }
        })

        blocks.append({"type": "divider"})

        # Results (limit to 10)
        for idx, result in enumerate(results[:10], 1):
            channel = result.get("channel_name", "unknown")
            user = result.get("user_name", "unknown")
            text = result.get("text", "")
            timestamp = result.get("timestamp", "")
            score = result.get("score", 0)
            url = result.get("url", "")

            # Truncate text if too long
            if len(text) > 150:
                text = text[:147] + "..."

            result_text = f"{idx}. <#{channel}> - @{user}"
            if url:
                result_text += f" - <{url}|View>"

            result_text += f"\n{text}"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": result_text
                }
            })

        # Show more indicator if there are more results
        if len(results) > 10:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"... and {len(results) - 10} more results"
                    }
                ]
            })

        return {
            "blocks": blocks,
            "text": f"Found {len(results)} results for: {query}"
        }

    @staticmethod
    def format_error_message(error_message: str) -> Dict[str, Any]:
        """Format error message for Slack"""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Error: {error_message}"
                }
            }
        ]

        return {
            "blocks": blocks,
            "text": f"Error: {error_message}"
        }

    @staticmethod
    def format_help_message(command: str = None) -> Dict[str, Any]:
        """Format help message for commands"""
        if command == "ask":
            text = """How to use /ask:

Ask the bot questions about your Slack workspace content. The bot will search through messages and provide an AI-generated answer with citations.

Examples:
• /ask What did Sarah say about the deployment?
• /ask When is the product launch scheduled?
• /ask What are the known issues with the API?

The bot will respond in a thread with relevant information and source links."""

        elif command == "find":
            text = """How to use /find:

Search for specific messages and content in your Slack workspace. The bot will return a list of relevant messages with links.

Examples:
• /find bug reports from last week
• /find API documentation
• /find deployment schedule

The bot will respond with a list of matching messages."""

        else:
            text = """Available Commands:

/ask [question]
Ask questions and get AI-powered answers with citations from your Slack workspace.

/find [search query]
Search for specific messages and content across your workspace.

You can also:
• Mention the bot: @bot what's the status?
• Send a direct message to the bot

The bot will always respond in a thread to keep conversations organized."""

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            }
        ]

        return {
            "blocks": blocks,
            "text": text
        }


# Global response formatter instance
response_formatter = ResponseFormatter()
