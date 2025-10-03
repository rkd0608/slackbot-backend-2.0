# Slack Intelligence System

Production-ready Intelligent Slack AI System with RAG, Knowledge Graph, and Multi-Stage Retrieval.

## Current Status: Phase 3 Complete ✅

### Completed Phases

#### Phase 1: Core Infrastructure ✅
- FastAPI application with lifecycle management
- Multi-layer storage (MySQL, Pinecone, Redis, RabbitMQ, S3)
- Database models (7 tables)
- Slack Events API integration
- Health check and monitoring endpoints
- Structured logging and Prometheus metrics

#### Phase 2: Data Ingestion Pipeline ✅
- Event processing for all Slack event types
- Message parsing with code detection, URL extraction, mentions
- File processing with text extraction (PDF, code, text files)
- Channel and user synchronization
- Historical data backfill with checkpoints
- Embedding generation with OpenAI
- Queue consumer framework with retry logic

#### Phase 3: Multi-Stage Retrieval System ✅
- Query understanding and intent classification
- Semantic vector search with Pinecone
- Keyword search with BM25-like scoring
- Entity-based retrieval
- Reciprocal Rank Fusion for result merging
- Feature-based reranking
- Context assembly and thread reconstruction
- Query API with caching and rate limiting

## Architecture

### Data Flow
1. **Ingestion**: Slack Events → RabbitMQ → Event Consumer → MySQL + Pinecone
2. **Query**: User Query → Analysis → Multi-Stage Retrieval → Reranking → Context Assembly
3. **Response**: Retrieved Context → LLM (Phase 4) → Formatted Response with Citations

### Technology Stack
- **Framework**: FastAPI 0.109
- **Database**: MySQL with async SQLAlchemy
- **Vector DB**: Pinecone (1536-dim embeddings)
- **Queue**: RabbitMQ with 3 consumer workers
- **Cache**: Redis (3-level caching)
- **Storage**: AWS S3
- **LLM**: OpenAI GPT-4 Turbo (Phase 4)
- **Embeddings**: OpenAI text-embedding-3-large
- **Monitoring**: Prometheus + Structlog

## Setup

### 1. Prerequisites
- Python 3.12+
- MySQL 8.0+
- Redis 7+
- RabbitMQ 3.12+
- AWS S3 bucket
- Pinecone account
- Slack app with Events API configured
- OpenAI API key

### 2. Installation

```bash
# Clone repository
cd slackbot-backend-v2.0

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
make install
# or
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env
```

Required environment variables:
- **Slack**: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, OAuth credentials
- **MySQL**: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
- **Pinecone**: `PINECONE_API_KEY`, `PINECONE_ENVIRONMENT`, `PINECONE_INDEX_NAME`
- **RabbitMQ**: `RABBITMQ_HOST`, credentials
- **AWS**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`
- **OpenAI**: `OPENAI_API_KEY`
- **Redis**: `REDIS_HOST`, `REDIS_PORT`
- **JWT**: `JWT_SECRET_KEY` (min 32 chars)

### 4. Database Setup

```bash
# Run migrations
make migrate
# or
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

### 5. Running the Application

**Option A: Docker Compose (Recommended)**
```bash
# Start all services (MySQL, Redis, RabbitMQ, App)
docker-compose up -d
```

**Option B: Manual Setup**
```bash
# Terminal 1: Start API server
make run
# or
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Start all consumer workers
make workers
# or individual workers:
make worker-event    # Event consumer
make worker-embed    # Embedding consumer
make worker-proc     # Processing consumer
```

### 6. Initial Data Backfill

```bash
# Trigger historical data backfill (1000 messages per channel)
curl -X POST http://localhost:8000/api/v1/admin/backfill/all

# Or run as standalone worker
make backfill
```

## API Endpoints

### Health & Status
- `GET /` - API information
- `GET /api/v1/health` - Basic health check
- `GET /api/v1/health/ready` - Readiness check (all dependencies)
- `GET /api/v1/health/stats` - System statistics
- `GET /metrics` - Prometheus metrics

### Slack Integration
- `POST /api/v1/slack/events` - Slack Events API webhook

### Query (Phase 3)
- `POST /api/v1/query` - Main query endpoint
  ```json
  {
    "query": "What did we decide about the database migration?",
    "user_id": "U123456",
    "top_k": 10,
    "include_context": true
  }
  ```
- `POST /api/v1/query/feedback` - Submit user feedback

### Admin
- `POST /api/v1/admin/backfill/channel/{channel_id}` - Backfill single channel
- `POST /api/v1/admin/backfill/all` - Backfill all channels
- `POST /api/v1/admin/backfill/resume` - Resume interrupted backfills
- `POST /api/v1/admin/sync/channels` - Sync all channels
- `POST /api/v1/admin/sync/channel/{channel_id}` - Sync single channel
- `POST /api/v1/admin/sync/user/{user_id}` - Sync single user

## Query Features (Phase 3)

### Intent Classification
- **Factual**: Direct questions about decisions, discussions
- **Code**: Looking for code snippets, implementations
- **Summary**: Requesting summaries or overviews
- **Timeline**: Understanding chronology of events
- **Who**: Attribution queries (who said/did something)
- **Comparison**: Comparing options or approaches
- **How-to**: Step-by-step guides or tutorials

### Query Understanding
- Entity extraction (projects, technologies, tickets)
- Temporal expression parsing ("last week", "yesterday", "Q3")
- Channel and user mention detection
- Code intent detection
- Question type classification

### Multi-Stage Retrieval
1. **Semantic Search**: Vector similarity with Pinecone (100 candidates)
2. **Keyword Search**: BM25-like database search (50 candidates)
3. **Entity-Based**: Knowledge graph traversal (30 candidates)
4. **Fusion**: Reciprocal Rank Fusion (RRF) with k=60
5. **Reranking**: Feature-based scoring (recency, importance, reactions, diversity)

### Context Assembly
- Thread reconstruction with parent message
- Long thread compression (keep relevant + context)
- Cross-thread connection detection
- Metadata enrichment (participants, channels, time span)

## Example Queries

```bash
# Factual query
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What was decided about the database migration last week?",
    "user_id": "U123456",
    "top_k": 5
  }'

# Code search
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me the SQL query for user analytics",
    "user_id": "U123456",
    "top_k": 3
  }'

# Timeline query
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "When did we discuss the API redesign?",
    "user_id": "U123456"
  }'
```

## Performance Metrics

### Target Latencies (95th percentile)
- **Total Query**: < 3 seconds
- **Retrieval**: < 500ms
- **Reranking**: < 300ms
- **LLM Generation** (Phase 4): < 1500ms

### Current Metrics
- **Semantic Search**: ~200ms (100 candidates)
- **Keyword Search**: ~150ms (50 candidates)
- **Entity Search**: ~100ms (30 candidates)
- **Fusion + Reranking**: ~50ms
- **Context Assembly**: ~100ms
- **Total (without LLM)**: ~600ms

## Monitoring

### Prometheus Metrics
```bash
# View metrics
curl http://localhost:8000/metrics

# Key metrics:
# - slack_messages_ingested_total
# - embeddings_generated_total
# - query_requests_total{intent_type}
# - query_latency_seconds{stage}
# - cache_hits_total{cache_level}
# - user_satisfaction_total{rating}
```

### Logs
```bash
# View structured logs (JSON format)
docker-compose logs -f app

# Filter by component
docker-compose logs -f app | grep "query_completed"
```

## Testing

```bash
# Run tests
make test
# or
pytest tests/

# Test query endpoint
python -c "
import requests
response = requests.post(
    'http://localhost:8000/api/v1/query',
    json={
        'query': 'What is our deployment process?',
        'user_id': 'U123456'
    }
)
print(response.json())
"
```

## Next Phase

### Phase 4: Response Generation (Coming Next)
- LLM-based response generation with citations
- Prompt engineering for different query intents
- Streaming responses
- Citation system with Slack deep links
- Answer validation and hallucination detection
- Multi-turn conversation support

### Phase 5: Advanced Features
- Automatic thread summarization
- Entity extraction and knowledge graph
- Proactive notifications
- Trend detection
- Decision tracking
- Code repository integration

## Troubleshooting

### Common Issues

**1. Database connection failed**
```bash
# Check MySQL is running
docker-compose ps mysql

# Test connection
mysql -h localhost -u slackbot -p
```

**2. Pinecone initialization failed**
```bash
# Verify API key and environment
python -c "from pinecone import Pinecone; pc = Pinecone(api_key='your-key'); print(pc.list_indexes())"
```

**3. RabbitMQ connection refused**
```bash
# Check RabbitMQ is running
docker-compose ps rabbitmq

# Access management UI
open http://localhost:15672
# Default credentials: guest/guest
```

**4. Workers not processing**
```bash
# Check queue status
docker-compose exec rabbitmq rabbitmqctl list_queues

# Restart workers
make workers
```

## Development

### Project Structure
```
app/
├── api/              # API endpoints
│   ├── health.py     # Health checks
│   ├── events.py     # Slack events webhook
│   ├── admin.py      # Admin operations
│   └── query.py      # Query endpoint (Phase 3)
├── core/             # Core infrastructure
│   ├── config.py     # Configuration
│   ├── database.py   # Database connections
│   ├── cache.py      # Redis cache
│   ├── queue.py      # RabbitMQ queue
│   ├── vector_db.py  # Pinecone client
│   └── storage.py    # S3 storage
├── models/           # Database models
│   ├── message.py    # Messages
│   ├── thread.py     # Threads
│   ├── channel.py    # Channels
│   ├── user.py       # Users
│   ├── entity.py     # Entities
│   ├── file.py       # Files
│   └── query_log.py  # Query logs
├── services/         # Business logic
│   ├── slack_client.py       # Slack API client
│   ├── slack_events.py       # Event handler
│   ├── message_processor.py  # Message processing
│   ├── file_processor.py     # File processing
│   ├── sync_service.py       # Sync operations
│   ├── backfill_service.py   # Historical backfill
│   ├── embedding_service.py  # Embedding generation
│   ├── query_service.py      # Query analysis (Phase 3)
│   ├── retrieval_service.py  # Multi-stage retrieval (Phase 3)
│   └── context_service.py    # Context assembly (Phase 3)
├── workers/          # Background workers
│   ├── base_consumer.py      # Base consumer class
│   ├── event_consumer.py     # Event processing
│   ├── embedding_consumer.py # Embedding generation
│   ├── processing_consumer.py # File & sync processing
│   ├── backfill_worker.py    # Backfill jobs
│   └── run_consumers.py      # Worker orchestration
└── utils/            # Utilities
    └── rate_limiter.py       # Rate limiting
```

## License

Proprietary - All rights reserved

## Support

For issues or questions, contact the development team.
