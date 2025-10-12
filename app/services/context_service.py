"""Context assembly and thread reconstruction service"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.message import Message
from app.models.thread import Thread
from app.models.channel import Channel
from app.core.logging import get_logger

logger = get_logger(__name__)


class ContextService:
    """Assembles context from retrieved results"""

    MAX_CONTEXT_TOKENS = 150000  # Reserve for LLM input
    MESSAGE_OVERHEAD = 200  # Estimated tokens per message metadata

    async def assemble_context(
        self,
        results: List[Dict[str, Any]],
        query_analysis: Dict[str, Any],
        db: AsyncSession,
        max_messages: int = 50
    ) -> Dict[str, Any]:
        """Assemble complete context from retrieval results"""

        logger.info("context_assembly_started", results=len(results))

        # Separate file results from message results
        file_results = [r for r in results if r.get("result_type") == "file"]
        message_results = [r for r in results if r.get("result_type") == "message"]

        # Fetch full message details
        messages = await self._fetch_messages(message_results, db)

        # Group by threads
        threads = self._group_by_threads(messages)

        # Reconstruct thread contexts
        thread_contexts = []
        for thread_ts, thread_messages in threads.items():
            context = await self._reconstruct_thread(thread_ts, thread_messages, db)
            thread_contexts.append(context)

        # Sort by relevance (based on original result scores)
        scored_contexts = self._score_contexts(thread_contexts, message_results)

        # Select top contexts within token budget
        selected_contexts = self._select_within_budget(
            scored_contexts,
            max_messages
        )

        # Generate meta-context
        meta_context = self._generate_meta_context(
            selected_contexts,
            query_analysis
        )

        logger.info(
            "context_assembled",
            threads=len(selected_contexts),
            total_messages=sum(len(c["messages"]) for c in selected_contexts),
            files=len(file_results)
        )

        context_dict = {
            "meta_context": meta_context,
            "thread_contexts": selected_contexts,
            "query_analysis": query_analysis
        }

        # Add file results if any
        if file_results:
            context_dict["file_results"] = file_results

        return context_dict

    async def _fetch_messages(
        self,
        results: List[Dict[str, Any]],
        db: AsyncSession
    ) -> List[Message]:
        """Fetch full message objects"""

        message_ids = [r["message_id"] for r in results if r.get("message_id")]

        if not message_ids:
            return []

        result = await db.execute(
            select(Message).where(Message.message_id.in_(message_ids))
        )

        return result.scalars().all()

    def _group_by_threads(self, messages: List[Message]) -> Dict[str, List[Message]]:
        """Group messages by thread"""

        threads = {}

        for msg in messages:
            # Use thread_ts as key, or message_id if not in thread
            thread_key = msg.thread_ts or msg.message_id

            if thread_key not in threads:
                threads[thread_key] = []

            threads[thread_key].append(msg)

        return threads

    async def _reconstruct_thread(
        self,
        thread_ts: str,
        relevant_messages: List[Message],
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Reconstruct complete thread context"""

        # Check if this is actually a thread
        is_thread = any(msg.thread_ts for msg in relevant_messages)

        if not is_thread:
            # Single message, no thread
            msg = relevant_messages[0]
            return {
                "thread_ts": thread_ts,
                "is_thread": False,
                "channel_id": msg.channel_id,
                "channel_name": msg.channel_name,
                "messages": [self._format_message(msg)],
                "summary": None,
                "metadata": {
                    "total_messages": 1,
                    "participants": [msg.user_id]
                }
            }

        # Fetch thread metadata
        result = await db.execute(
            select(Thread).where(Thread.thread_ts == thread_ts)
        )
        thread = result.scalar_one_or_none()

        # Fetch all thread messages (not just relevant ones)
        result = await db.execute(
            select(Message)
            .where(Message.thread_ts == thread_ts)
            .order_by(Message.timestamp)
        )
        all_messages = result.scalars().all()

        # Format messages
        formatted_messages = [self._format_message(msg) for msg in all_messages]

        # If thread is too long, summarize middle portions
        if len(formatted_messages) > 10:
            formatted_messages = self._compress_long_thread(
                formatted_messages,
                relevant_messages
            )

        return {
            "thread_ts": thread_ts,
            "is_thread": True,
            "channel_id": all_messages[0].channel_id if all_messages else None,
            "channel_name": all_messages[0].channel_name if all_messages else None,
            "messages": formatted_messages,
            "summary": thread.summary if thread else None,
            "metadata": {
                "total_messages": len(all_messages),
                "participants": thread.participant_ids if thread else [],
                "reply_count": thread.reply_count if thread else 0,
                "importance_score": thread.importance_score if thread else 0.0
            }
        }

    def _format_message(self, msg: Message) -> Dict[str, Any]:
        """Format message for context"""

        return {
            "message_id": msg.message_id,
            "user_id": msg.user_id,
            "user_name": msg.user_name,
            "timestamp": msg.timestamp.isoformat(),
            "text": msg.text,
            "has_code": msg.has_code,
            "code_languages": msg.code_languages,
            "reactions": msg.reactions,
            "reaction_count": msg.reaction_count,
            "links": msg.links,
            "file_ids": msg.file_ids,
            "mentioned_users": msg.mentioned_users,
            "importance_score": msg.importance_score
        }

    def _compress_long_thread(
        self,
        all_messages: List[Dict[str, Any]],
        relevant_messages: List[Message]
    ) -> List[Dict[str, Any]]:
        """Compress long threads by keeping relevant messages and summarizing gaps"""

        relevant_ids = {msg.message_id for msg in relevant_messages}

        compressed = []

        # Always include parent (first message)
        compressed.append(all_messages[0])

        # Add relevant messages with context
        for i, msg in enumerate(all_messages[1:], 1):
            if msg["message_id"] in relevant_ids:
                # Add message with 1 message context before/after
                if i > 1 and all_messages[i-1]["message_id"] not in relevant_ids:
                    compressed.append(all_messages[i-1])

                compressed.append(msg)

                if i < len(all_messages) - 1 and all_messages[i+1]["message_id"] not in relevant_ids:
                    compressed.append(all_messages[i+1])

        # If thread is still too long, add summary placeholder
        if len(compressed) < len(all_messages):
            compressed.append({
                "message_id": "summary",
                "text": f"[{len(all_messages) - len(compressed)} additional messages in thread]",
                "is_summary": True
            })

        return compressed

    def _score_contexts(
        self,
        thread_contexts: List[Dict[str, Any]],
        original_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score thread contexts based on original result scores"""

        # Create score map
        score_map = {
            r["message_id"]: r.get("final_score", r.get("rrf_score", r.get("score", 0.0)))
            for r in original_results
        }

        # Score each context
        for context in thread_contexts:
            # Sum scores of all messages in thread
            total_score = 0.0
            for msg in context["messages"]:
                if not msg.get("is_summary"):
                    msg_score = score_map.get(msg["message_id"], 0.0)
                    total_score += msg_score

            # Boost for thread importance
            importance_boost = context.get("metadata", {}).get("importance_score", 0.0) / 10.0

            context["relevance_score"] = total_score + importance_boost

        # Sort by score
        thread_contexts.sort(key=lambda x: x["relevance_score"], reverse=True)

        return thread_contexts

    def _select_within_budget(
        self,
        contexts: List[Dict[str, Any]],
        max_messages: int
    ) -> List[Dict[str, Any]]:
        """Select contexts within token budget"""

        selected = []
        total_messages = 0

        for context in contexts:
            context_msg_count = len([m for m in context["messages"] if not m.get("is_summary")])

            if total_messages + context_msg_count <= max_messages:
                selected.append(context)
                total_messages += context_msg_count
            else:
                # Check if we can fit at least the parent message
                if total_messages < max_messages:
                    # Truncate context to fit budget
                    remaining = max_messages - total_messages
                    context["messages"] = context["messages"][:remaining]
                    selected.append(context)
                    break

        return selected

    def _generate_meta_context(
        self,
        contexts: List[Dict[str, Any]],
        query_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate meta-information about retrieved contexts"""

        # Get unique channels
        channels = list(set(
            c["channel_name"]
            for c in contexts
            if c.get("channel_name")
        ))

        # Get unique participants
        all_participants = []
        for context in contexts:
            all_participants.extend(context.get("metadata", {}).get("participants", []))
        unique_participants = list(set(all_participants))

        # Time span
        timestamps = []
        for context in contexts:
            for msg in context["messages"]:
                if not msg.get("is_summary") and msg.get("timestamp"):
                    timestamps.append(msg["timestamp"])

        time_span = None
        if timestamps:
            timestamps.sort()
            time_span = {
                "earliest": timestamps[0],
                "latest": timestamps[-1]
            }

        # Cross-thread connections
        connections = self._find_cross_thread_connections(contexts)

        return {
            "total_threads": len(contexts),
            "total_messages": sum(len(c["messages"]) for c in contexts),
            "channels": channels,
            "participants": unique_participants,
            "time_span": time_span,
            "connections": connections,
            "query_intents": query_analysis.get("intents", [])
        }

    def _find_cross_thread_connections(
        self,
        contexts: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Identify connections between threads"""

        connections = []

        # Check for same users across threads
        user_threads = {}
        for context in contexts:
            participants = context.get("metadata", {}).get("participants", [])
            for user in participants:
                if user not in user_threads:
                    user_threads[user] = []
                user_threads[user].append(context["thread_ts"])

        # Users appearing in multiple threads
        for user, threads in user_threads.items():
            if len(threads) > 1:
                connections.append({
                    "type": "user_participation",
                    "user": user,
                    "thread_count": len(threads)
                })

        # Threads in same channel (related topic)
        channel_threads = {}
        for context in contexts:
            channel = context.get("channel_name")
            if channel:
                if channel not in channel_threads:
                    channel_threads[channel] = []
                channel_threads[channel].append(context["thread_ts"])

        for channel, threads in channel_threads.items():
            if len(threads) > 1:
                connections.append({
                    "type": "same_channel",
                    "channel": channel,
                    "thread_count": len(threads)
                })

        return connections


# Global context service instance
context_service = ContextService()
