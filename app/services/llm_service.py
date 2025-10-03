"""LLM response generation service with streaming support"""
from typing import Dict, Any, List, AsyncGenerator, Optional
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger
from app.core.monitoring import query_latency
import time
import re

logger = get_logger(__name__)


class LLMService:
    """Handles LLM-based response generation"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_llm_model
        self.max_tokens = 4096
        self.temperature = 0.3  # Lower for more factual responses

    async def generate_response(
        self,
        prompt: Dict[str, Any],
        stream: bool = False
    ) -> Dict[str, Any]:
        """Generate response from LLM"""

        start_time = time.time()

        try:
            messages = [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]}
            ]

            if stream:
                # Return async generator for streaming
                return {
                    "stream": self._stream_response(messages),
                    "intent": prompt["intent"]
                }
            else:
                # Non-streaming response
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stream=False
                )

                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                latency = time.time() - start_time
                query_latency.labels(stage="llm").observe(latency)

                logger.info(
                    "llm_response_generated",
                    model=self.model,
                    tokens=response.usage.total_tokens,
                    latency_ms=int(latency * 1000),
                    finish_reason=finish_reason
                )

                return {
                    "content": content,
                    "finish_reason": finish_reason,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    },
                    "latency_ms": int(latency * 1000),
                    "intent": prompt["intent"]
                }

        except Exception as e:
            logger.error("llm_generation_error", error=str(e))
            raise

    async def _stream_response(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        """Stream response from LLM"""

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error("llm_streaming_error", error=str(e))
            raise

    async def generate_with_conversation(
        self,
        prompt: Dict[str, Any],
        conversation_history: List[Dict[str, str]],
        stream: bool = False
    ) -> Dict[str, Any]:
        """Generate response with conversation history"""

        messages = [
            {"role": "system", "content": prompt["system"]}
        ]

        # Add conversation history
        messages.extend(conversation_history)

        # Add current query
        messages.append({"role": "user", "content": prompt["user"]})

        # Truncate if too long (keep system + recent history)
        max_history_tokens = 8000  # Rough estimate
        if len(conversation_history) > 10:
            # Keep first system message and last 10 exchanges
            messages = [messages[0]] + messages[-20:]

        if stream:
            return {
                "stream": self._stream_response(messages),
                "intent": prompt["intent"]
            }
        else:
            start_time = time.time()

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=False
            )

            content = response.choices[0].message.content
            latency = time.time() - start_time

            logger.info(
                "conversation_response_generated",
                turns=len(conversation_history) // 2,
                tokens=response.usage.total_tokens,
                latency_ms=int(latency * 1000)
            )

            return {
                "content": content,
                "finish_reason": response.choices[0].finish_reason,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "latency_ms": int(latency * 1000),
                "intent": prompt["intent"]
            }


# Global LLM service instance
llm_service = LLMService()
