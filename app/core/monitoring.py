"""Prometheus metrics and monitoring"""
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, Summary
from app.core.logging import get_logger

logger = get_logger(__name__)

# Create registry
registry = CollectorRegistry()

# System Metrics
ingestion_rate = Counter(
    'slack_messages_ingested_total',
    'Total number of Slack messages ingested',
    ['event_type'],
    registry=registry
)

ingestion_lag = Histogram(
    'slack_ingestion_lag_seconds',
    'Time between Slack event and ingestion',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    registry=registry
)

processing_rate = Counter(
    'slack_messages_processed_total',
    'Total number of messages processed',
    ['status'],
    registry=registry
)

embedding_generation_rate = Counter(
    'embeddings_generated_total',
    'Total number of embeddings generated',
    registry=registry
)

# Storage Metrics
vector_count = Gauge(
    'vector_db_count',
    'Number of vectors in database',
    registry=registry
)

database_size = Gauge(
    'database_size_bytes',
    'Database size in bytes',
    ['database'],
    registry=registry
)

# Query Metrics
query_requests = Counter(
    'query_requests_total',
    'Total number of query requests',
    ['intent_type'],
    registry=registry
)

query_latency = Histogram(
    'query_latency_seconds',
    'Query latency distribution',
    ['stage'],
    buckets=[0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
    registry=registry
)

cache_hits = Counter(
    'cache_hits_total',
    'Total number of cache hits',
    ['cache_level'],
    registry=registry
)

cache_misses = Counter(
    'cache_misses_total',
    'Total number of cache misses',
    ['cache_level'],
    registry=registry
)

# Quality Metrics
retrieval_precision = Histogram(
    'retrieval_precision',
    'Retrieval precision at k',
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=registry
)

user_satisfaction = Counter(
    'user_satisfaction_total',
    'User satisfaction feedback',
    ['rating'],
    registry=registry
)

# Error Metrics
errors = Counter(
    'errors_total',
    'Total number of errors',
    ['component', 'error_type'],
    registry=registry
)

# Rate Limiting
rate_limit_exceeded = Counter(
    'rate_limit_exceeded_total',
    'Total number of rate limit violations',
    ['user_id'],
    registry=registry
)

# Phase 4: LLM & Answer Metrics
answer_requests = Counter(
    'answer_requests_total',
    'Total number of answer generation requests',
    ['intent', 'streaming'],
    registry=registry
)

answer_quality = Histogram(
    'answer_quality_score',
    'Answer quality score distribution',
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=registry
)

citation_count = Histogram(
    'answer_citation_count',
    'Number of citations per answer',
    buckets=[0, 1, 2, 3, 5, 7, 10, 15, 20],
    registry=registry
)

llm_token_usage = Counter(
    'llm_tokens_used_total',
    'Total LLM tokens used',
    ['type'],  # prompt, completion
    registry=registry
)

llm_latency = Summary(
    'llm_generation_latency_seconds',
    'LLM generation latency',
    registry=registry
)

conversation_turns = Histogram(
    'conversation_turn_count',
    'Number of turns in conversations',
    buckets=[1, 2, 3, 5, 7, 10, 15, 20],
    registry=registry
)

hallucination_detections = Counter(
    'hallucination_detections_total',
    'Total number of potential hallucinations detected',
    ['indicator_type'],
    registry=registry
)

# AI Quality Assurance Metrics (Phase 2.1)
feedback_responses = Counter(
    'feedback_responses_total',
    'Total user feedback responses',
    ['feedback_type'],  # thumbs_up, thumbs_down
    registry=registry
)

feedback_rate = Gauge(
    'feedback_rate',
    'Percentage of queries that receive feedback',
    registry=registry
)

avg_retrieval_score = Gauge(
    'avg_retrieval_score',
    'Average retrieval relevance score (top result)',
    ['time_window'],  # 1h, 24h, 7d
    registry=registry
)

query_rewrite_rate = Gauge(
    'query_rewrite_rate',
    'Percentage of queries that were rewritten',
    registry=registry
)

query_expansion_rate = Gauge(
    'query_expansion_rate',
    'Percentage of queries that were expanded',
    registry=registry
)

# Note: Evaluation metrics (pass_rate, precision, recall, mrr, ndcg) removed
# Evaluation system was replaced with knowledge graph

retrieval_no_results = Counter(
    'retrieval_no_results_total',
    'Queries that returned zero results',
    ['query_type'],
    registry=registry
)

retrieval_low_confidence = Counter(
    'retrieval_low_confidence_total',
    'Queries with top score below threshold',
    ['threshold'],  # 0.3, 0.5, 0.7
    registry=registry
)
