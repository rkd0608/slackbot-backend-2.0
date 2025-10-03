"""Pydantic schemas for API requests/responses"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Query request schema"""
    query: str = Field(..., min_length=1, max_length=1000, description="The user's query")
    user_id: str = Field(..., description="User ID making the query")
    top_k: Optional[int] = Field(default=10, ge=1, le=50, description="Number of results to return")
    include_context: Optional[bool] = Field(default=True, description="Include full thread context")


class MessageResult(BaseModel):
    """Message result schema"""
    message_id: str
    channel_id: str
    channel_name: Optional[str]
    user_id: str
    user_name: Optional[str]
    timestamp: str
    text: Optional[str]
    relevance_score: float
    has_code: bool
    reactions: Optional[List[Dict[str, Any]]]
    permalink: Optional[str]


class ThreadContext(BaseModel):
    """Thread context schema"""
    thread_ts: str
    is_thread: bool
    channel_name: Optional[str]
    messages: List[Dict[str, Any]]
    summary: Optional[str]
    relevance_score: float
    metadata: Dict[str, Any]


class QueryAnalysis(BaseModel):
    """Query analysis schema"""
    intents: List[str]
    entities: List[Dict[str, str]]
    temporal: Optional[Dict[str, Any]]
    channels: List[str]
    users: List[str]
    has_code_intent: bool
    question_type: str


class QueryResponse(BaseModel):
    """Query response schema"""
    query_id: str
    query: str
    analysis: QueryAnalysis
    results: List[MessageResult]
    thread_contexts: Optional[List[ThreadContext]]
    meta_context: Optional[Dict[str, Any]]
    total_results: int
    latency_ms: int


class SearchStats(BaseModel):
    """Search statistics"""
    total_queries: int
    avg_latency_ms: float
    cache_hit_rate: float
    top_intents: List[Dict[str, Any]]
