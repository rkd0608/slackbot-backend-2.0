"""Format responses for Slack Block Kit"""
from typing import List, Dict, Any, Optional
import re
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResponseFormatter:
    """Formats bot responses for Slack using Block Kit"""

    @staticmethod
    def _parse_answer_blocks(answer: str) -> List[Dict[str, Any]]:
        """
        Parse answer into alternating text and code blocks.

        Returns:
            List of dicts with 'type' ('text' or 'code'), 'content', and optionally 'language'
        """
        # Handle empty or None answer
        if not answer or not isinstance(answer, str):
            return [{'type': 'text', 'content': ''}]

        parts = []

        try:
            # Split by code fence markers
            segments = answer.split('```')

            # If there are no code blocks, return the entire answer as text
            if len(segments) == 1:
                return [{'type': 'text', 'content': answer}]

            for idx, segment in enumerate(segments):
                # Skip empty segments
                if not segment:
                    continue

                if idx % 2 == 0:
                    # Text segment
                    if segment.strip():
                        parts.append({
                            'type': 'text',
                            'content': segment
                        })
                else:
                    # Code segment
                    # Handle edge case where segment might not have newlines
                    if '\n' in segment:
                        lines = segment.split('\n', 1)
                        language = lines[0].strip() if len(lines) > 0 else ''
                        code_content = lines[1] if len(lines) > 1 else segment
                    else:
                        # No newline after language identifier
                        language = ''
                        code_content = segment

                    parts.append({
                        'type': 'code',
                        'content': code_content,
                        'language': language
                    })

            # If parts is empty, return the original answer as text
            if not parts:
                return [{'type': 'text', 'content': answer}]

            return parts

        except Exception as e:
            # If parsing fails for any reason, return the entire answer as text
            logger.error("parse_answer_blocks_error", error=str(e))
            return [{'type': 'text', 'content': answer}]

    @staticmethod
    def _split_text_smart(text: str, max_length: int) -> List[str]:
        """
        Split text at paragraph boundaries to respect length limit.
        """
        if len(text) <= max_length:
            return [text]

        chunks = []
        paragraphs = text.split('\n\n')
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_length:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                # If single paragraph is too long, split by sentences
                if len(para) > max_length:
                    sentences = re.split(r'([.!?]\s+)', para)
                    temp = ""
                    for sent in sentences:
                        if len(temp) + len(sent) <= max_length:
                            temp += sent
                        else:
                            if temp:
                                chunks.append(temp)
                            temp = sent
                    if temp:
                        current_chunk = temp
                    else:
                        current_chunk = ""
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @staticmethod
    def _split_code_block(code: str, max_length: int) -> List[str]:
        """
        Split code block at line boundaries to respect length limit.
        """
        if len(code) <= max_length:
            return [code]

        chunks = []
        lines = code.split('\n')
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 <= max_length:
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

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

        # Convert ```code blocks``` to Slack format (keep language identifier)
        # Slack format: ```language\ncode\n``` or just ```\ncode\n```
        # Keep as-is since Slack supports standard markdown code blocks
        # Just ensure proper newlines are preserved

        # Convert [text](url) to <url|text>
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<\2|\1>', text)

        # Convert numbered lists to better formatting
        text = re.sub(r'^(\d+)\.\s+', r'*\1.* ', text, flags=re.MULTILINE)

        # Convert bullet points - fix spacing
        text = re.sub(r'^[•\-]\s+', r'• ', text, flags=re.MULTILINE)

        return text

    @staticmethod
    def _get_source_emoji(source: str) -> str:
        """Get emoji indicator for a source type"""
        source_emojis = {
            'slack': '💬',
            'github': '💻',
            'jira': '🎫',
            'confluence': '📄',
            'linear': '📋',
            'notion': '📝'
        }
        return source_emojis.get(source.lower(), '📌')

    @staticmethod
    def format_answer_response(
        answer: str,
        citations: List[Dict[str, Any]],
        confidence: float = None,
        query_id: str = None
    ) -> Dict[str, Any]:
        """Format LLM answer with citations as Slack blocks"""
        blocks = []

        # Parse answer into text and code blocks
        parts = ResponseFormatter._parse_answer_blocks(answer)

        # Process each part
        for part in parts:
            if part['type'] == 'text':
                # Convert markdown to Slack format
                formatted_text = ResponseFormatter.convert_to_slack_markdown(part['content'])

                # Split text if it exceeds limit
                if len(formatted_text) <= 2900:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": formatted_text
                        }
                    })
                else:
                    # Split long text at paragraph boundaries
                    chunks = ResponseFormatter._split_text_smart(formatted_text, 2900)
                    for chunk in chunks:
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": chunk
                            }
                        })

            elif part['type'] == 'code':
                # Use Slack's rich text block for code (better formatting, syntax highlighting)
                code_content = part['content']
                language = part.get('language', '')

                # Add a small header before code block
                if language:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Code ({language}):*"
                        }
                    })

                # Keep code as one block, no matter the length
                blocks.append({
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_preformatted",
                            "elements": [
                                {
                                    "type": "text",
                                    "text": code_content
                                }
                            ]
                        }
                    ]
                })

        # Add divider if there are citations
        if citations:
            blocks.append({"type": "divider"})

            # Citations section with source indicators
            citation_lines = []
            for idx, citation in enumerate(citations[:5], 1):  # Limit to 5 citations
                source = citation.get("source", "slack")
                source_emoji = ResponseFormatter._get_source_emoji(source)

                # Handle different source types
                if source == "slack":
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

                    # Build citation line with source emoji
                    citation_text = f"{source_emoji} *{idx}.* {channel_ref}"

                    # Add user mention
                    if user and user != "unknown":
                        citation_text += f" - {user}"

                    # Add link or timestamp
                    if url:
                        citation_text += f" - <{url}|View>"
                    elif timestamp:
                        citation_text += f" - _{timestamp}_"

                elif source == "github":
                    # GitHub citation format
                    title = citation.get("title", "Untitled")
                    url = citation.get("url", "")
                    node_type = citation.get("node_type", "code_file")
                    author = citation.get("author", "")

                    citation_text = f"{source_emoji} *{idx}.* {title}"

                    if node_type:
                        citation_text += f" _{node_type.replace('_', ' ')}_"

                    if author:
                        citation_text += f" - {author}"

                    if url:
                        citation_text += f" - <{url}|View on GitHub>"

                else:
                    # Generic format for other sources
                    title = citation.get("title", "Untitled")
                    url = citation.get("url", "")
                    author = citation.get("author", "")

                    citation_text = f"{source_emoji} *{idx}.* {title}"

                    if author:
                        citation_text += f" - {author}"

                    if url:
                        citation_text += f" - <{url}|View>"

                citation_lines.append(citation_text)

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Sources:*\n" + "\n".join(citation_lines)
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
                "text": f"*Search Results for:* {query}\n\nFound {len(results)} relevant messages:"
            }
        })

        blocks.append({"type": "divider"})

        # Results (limit to 15 to avoid message being too long)
        display_limit = min(15, len(results))
        for idx, result in enumerate(results[:display_limit], 1):
            # Get channel info
            channel_name = result.get("channel_name") or result.get("channel", "unknown")
            channel_id = result.get("channel_id", "")

            # Get user info
            user_name = result.get("user_name") or result.get("user", "unknown")

            # Get message info
            text = result.get("text", "")
            timestamp = result.get("timestamp", "")
            score = result.get("score", 0)
            message_id = result.get("message_id", "")

            # Format channel reference
            if channel_id:
                channel_ref = f"<#{channel_id}>"
            else:
                channel_ref = f"#{channel_name}"

            # Build message URL if we have the required info
            url = ""
            if channel_id and message_id:
                message_ts = message_id.replace(".", "")
                url = f"https://slack.com/archives/{channel_id}/p{message_ts}"

            # Truncate text if too long
            if len(text) > 150:
                text = text[:147] + "..."

            # Build result text
            result_text = f"*{idx}.* {channel_ref} - @{user_name}"
            if url:
                result_text += f" - <{url}|View Message>"
            elif timestamp:
                result_text += f" - _{timestamp}_"

            result_text += f"\n{text}"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": result_text
                }
            })

        # Show more indicator if there are more results
        if len(results) > display_limit:
            remaining = len(results) - display_limit
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_... and {remaining} more result{'s' if remaining > 1 else ''}_"
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
