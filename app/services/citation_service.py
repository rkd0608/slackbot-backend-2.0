"""Citation extraction and formatting service"""
import re
from typing import List, Dict, Any, Tuple
from app.core.logging import get_logger

logger = get_logger(__name__)


class CitationService:
    """Extracts and formats citations from LLM responses"""

    # Citation pattern: [Channel, @User, timestamp]
    CITATION_PATTERN = r'\[([^,]+),\s*@?([^,]+),\s*([^\]]+)\]'

    def extract_citations(self, response_text: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Extract citations from response and replace with numbered references"""

        citations = []
        citation_map = {}

        def replace_citation(match):
            channel = match.group(1).strip()
            user = match.group(2).strip()
            timestamp = match.group(3).strip()

            # Create citation key
            citation_key = f"{channel}|{user}|{timestamp}"

            # Check if we've seen this citation before
            if citation_key not in citation_map:
                citation_num = len(citations) + 1
                citation_map[citation_key] = citation_num

                citations.append({
                    "number": citation_num,
                    "channel": channel,
                    "user": user,
                    "timestamp": timestamp,
                    "original": match.group(0)
                })
            else:
                citation_num = citation_map[citation_key]

            return f"[{citation_num}]"

        # Replace all citations with numbered references
        processed_text = re.sub(self.CITATION_PATTERN, replace_citation, response_text)

        logger.info("citations_extracted", count=len(citations))

        return processed_text, citations

    def format_citations(
        self,
        citations: List[Dict[str, Any]],
        thread_contexts: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Format citations with Slack deep links"""

        formatted = []

        for citation in citations:
            # Try to find matching message in context
            message_link = self._find_message_link(citation, thread_contexts)

            formatted.append({
                "number": citation["number"],
                "channel": citation["channel"],
                "user": citation["user"],
                "channel_name": citation["channel"].lstrip('#'),  # For response formatter
                "user_name": citation["user"].lstrip('@'),  # For response formatter
                "timestamp": citation["timestamp"],
                "url": message_link,
                "link": message_link,
                "text": self._format_citation_text(citation, message_link)
            })

        return formatted

    def _find_message_link(
        self,
        citation: Dict[str, Any],
        thread_contexts: List[Dict[str, Any]]
    ) -> str:
        """Find Slack deep link for cited message"""

        channel_name = citation["channel"].lstrip('#')
        user_name = citation["user"].lstrip('@')
        timestamp_str = citation["timestamp"]

        # Search through thread contexts
        for thread_ctx in thread_contexts:
            if thread_ctx.get("channel_name") != channel_name:
                continue

            for message in thread_ctx.get("messages", []):
                # Match by user and approximate timestamp
                if (message.get("user_name") == user_name and
                    timestamp_str in message.get("timestamp", "")):

                    # Generate Slack deep link
                    channel_id = thread_ctx.get("channel_id", "")
                    message_id = message.get("message_id", "")

                    if channel_id and message_id:
                        # Convert timestamp format for URL
                        message_ts = message_id.replace(".", "")
                        return f"https://slack.com/archives/{channel_id}/p{message_ts}"

        # Fallback: generic channel link
        return f"https://slack.com/app_redirect?channel={channel_name}"

    def _format_citation_text(self, citation: Dict[str, Any], link: str) -> str:
        """Format citation as readable text"""

        if link and "slack.com/archives" in link:
            return f"[{citation['number']}] #{citation['channel']} - @{citation['user']} - {citation['timestamp']}"
        else:
            return f"[{citation['number']}] #{citation['channel']} - @{citation['user']} - {citation['timestamp']} (link unavailable)"

    def validate_citations(
        self,
        response_text: str,
        thread_contexts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate that all citations reference actual messages in context"""

        # Extract citations
        _, citations = self.extract_citations(response_text)

        valid_count = 0
        invalid_citations = []

        for citation in citations:
            # Check if citation matches any message in context
            is_valid = False

            channel_name = citation["channel"].lstrip('#')
            user_name = citation["user"].lstrip('@')

            for thread_ctx in thread_contexts:
                if thread_ctx.get("channel_name") != channel_name:
                    continue

                for message in thread_ctx.get("messages", []):
                    if message.get("user_name") == user_name:
                        is_valid = True
                        break

                if is_valid:
                    break

            if is_valid:
                valid_count += 1
            else:
                invalid_citations.append(citation)

        validation_score = valid_count / len(citations) if citations else 1.0

        logger.info(
            "citations_validated",
            total=len(citations),
            valid=valid_count,
            invalid=len(invalid_citations),
            score=validation_score
        )

        return {
            "total_citations": len(citations),
            "valid_citations": valid_count,
            "invalid_citations": invalid_citations,
            "validation_score": validation_score
        }


# Global citation service instance
citation_service = CitationService()
