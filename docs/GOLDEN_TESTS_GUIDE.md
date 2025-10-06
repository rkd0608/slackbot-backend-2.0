# Golden Test Set & Evaluation Guide

This guide explains how to create golden test cases and run automated evaluations to track retrieval quality.

## Overview

Golden tests are curated query-answer pairs that represent ideal system behavior. They help you:
- Track retrieval quality over time
- Detect regressions before deployment
- Compare different retrieval strategies
- Optimize system parameters

## Quick Start

### Option 1: Interactive Script (Recommended)

Run the interactive script to create tests and run evaluations:

```bash
docker-compose exec app python scripts/create_golden_tests.py
```

This provides a menu with options to:
1. Create test from existing query log (uses queries users rated highly)
2. Create test manually
3. List all golden tests
4. Run evaluation

### Option 2: REST API

#### Create a Golden Test

```bash
curl -X POST http://localhost:8000/api/v1/evaluation/golden-test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what did the team discuss about the API deployment?",
    "expected_message_ids": ["1234567.890123", "1234567.890456"],
    "test_name": "API deployment discussion retrieval",
    "query_type": "summary",
    "category": "engineering",
    "difficulty": "medium",
    "min_relevance_score": 0.7,
    "required_in_top_k": 5
  }'
```

#### Run Evaluation

```bash
curl -X POST http://localhost:8000/api/v1/evaluation/run \
  -H "Content-Type: application/json" \
  -d '{
    "run_name": "Weekly evaluation - 2025-01-15",
    "model_version": "gpt-4o-mini",
    "top_k": 10
  }'
```

#### Get Results

```bash
curl http://localhost:8000/api/v1/evaluation/run/{run_id}
```

## Metrics Explained

### Precision@5
- **What**: Proportion of top 5 results that are relevant
- **Range**: 0.0 to 1.0 (higher is better)
- **Example**: If 4 out of top 5 results are relevant, P@5 = 0.8
- **Good threshold**: ≥ 0.6

### Recall@5
- **What**: Proportion of all relevant documents that appear in top 5
- **Range**: 0.0 to 1.0 (higher is better)
- **Example**: If 3 relevant docs exist and 2 are in top 5, R@5 = 0.67
- **Good threshold**: ≥ 0.5

### MRR (Mean Reciprocal Rank)
- **What**: 1 / position of first relevant result
- **Range**: 0.0 to 1.0 (higher is better)
- **Example**: If first relevant doc is at position 3, RR = 0.33
- **Good threshold**: ≥ 0.5

### NDCG@5 (Normalized Discounted Cumulative Gain)
- **What**: Quality-weighted ranking metric (rewards relevant docs at top)
- **Range**: 0.0 to 1.0 (higher is better)
- **Formula**: Discounts relevance by position (log scale)
- **Good threshold**: ≥ 0.7

## Best Practices

### Creating Good Golden Tests

1. **Start with real user queries**: Use highly-rated queries from production
2. **Cover diverse scenarios**:
   - Different query types (factual, summary, code, timeline)
   - Different channels/teams
   - Different difficulty levels
3. **Maintain 30-50 tests minimum** for statistical significance
4. **Review and update** quarterly as your data evolves

### Building Your Test Set

**Week 1**: Create 10-15 tests from highly-rated queries
```bash
docker-compose exec app python scripts/create_golden_tests.py
# Select option 1, choose from recent 5-star queries
```

**Week 2-4**: Add 5-10 manual tests covering edge cases
```bash
# Option 2 in the script, or use API
```

**Ongoing**: Add tests when users report poor results

### Running Evaluations

**Before deployment**:
```bash
docker-compose exec app python scripts/create_golden_tests.py
# Select option 4: Run evaluation
```

**Weekly automated** (add to CI/CD):
```bash
curl -X POST http://localhost:8000/api/v1/evaluation/run \
  -H "Content-Type: application/json" \
  -d '{"run_name": "Weekly - $(date +%Y-%m-%d)"}'
```

## Example Workflow

### 1. Initial Setup (First Week)

```bash
# Run the interactive script
docker-compose exec app python scripts/create_golden_tests.py

# Menu appears:
# 1. Create test from existing query log
# 2. Create test manually
# 3. List all golden tests
# 4. Run evaluation
# 5. Exit

# Select option 1
# The script shows your highly-rated queries (rating ≥ 4)
# Select a query to convert into a golden test

# Repeat for 10-15 queries covering different categories
```

### 2. Baseline Evaluation

```bash
# Run evaluation to establish baseline
# Select option 4 from the menu

# Results will show:
# - Pass rate: 85% (17/20 tests passed)
# - Precision@5: 0.75
# - Recall@5: 0.68
# - MRR: 0.82
# - NDCG@5: 0.73
```

### 3. Make Changes & Re-evaluate

```bash
# After changing retrieval settings in app/core/config.py
# or modifying query analysis logic

# Run evaluation again
docker-compose exec app python scripts/create_golden_tests.py
# Option 4

# Compare metrics to baseline
# If metrics improve → deploy
# If metrics decline → revert changes
```

## Test Parameters Explained

### `expected_message_ids`
List of message IDs that should be retrieved for this query. Get these from:
- Slack message URLs (last part after last slash)
- Query log `result_message_ids` field
- Database `messages` table

### `min_relevance_score`
Minimum Precision@5 required to pass (default: 0.7)
- Easy tests: 0.8+
- Medium tests: 0.7
- Hard tests: 0.6

### `required_in_top_k`
All expected messages must appear in top K results (default: 5)
- Factual queries: 3 (should find exact answer quickly)
- Summary queries: 5-10 (needs multiple sources)

### `category`
Group tests for targeted evaluation:
- `engineering` - technical discussions, code, deployments
- `product` - features, roadmap, user feedback
- `support` - customer issues, troubleshooting
- `other` - general queries

## Advanced Usage

### Filter by Category

```bash
# Run evaluation only for engineering tests
curl -X POST http://localhost:8000/api/v1/evaluation/run \
  -d '{"category_filter": "engineering"}'
```

### Track Over Time

```sql
-- Get evaluation trend
SELECT
  DATE(started_at) as date,
  avg_precision_at_5,
  avg_mrr,
  passed_tests * 100.0 / total_tests as pass_rate
FROM evaluation_runs
WHERE completed_at IS NOT NULL
ORDER BY started_at DESC
LIMIT 30;
```

### Find Failing Tests

```sql
-- Identify consistently failing tests
SELECT
  t.test_name,
  t.query,
  COUNT(*) as fail_count
FROM evaluation_results r
JOIN golden_tests t ON r.test_id = t.test_id
WHERE r.passed = 0
GROUP BY r.test_id
HAVING fail_count >= 3
ORDER BY fail_count DESC;
```

## Troubleshooting

### "No active tests found"
- Create tests first using option 1 or 2
- Check if tests are active: `SELECT * FROM golden_tests WHERE is_active = 1`

### "No highly-rated queries found"
- Users need to rate bot responses using thumbs up/down buttons
- Or create tests manually (option 2)

### Low pass rates
- Review failed tests: Check `evaluation_results` table
- Adjust `min_relevance_score` if tests are too strict
- Check if expected message IDs are correct

### Metrics are NULL
- Means no expected messages were found
- Verify message IDs exist in your database
- Check if messages were embedded (have `vector_id`)

## Next Steps

After setting up golden tests:

1. **Phase 2.1**: Add production monitoring metrics (Prometheus)
   - Track live query metrics
   - Alert on quality degradation

2. **Phase 2.2**: Create Grafana dashboards
   - Visualize trends over time
   - Compare evaluation runs

3. **Automated CI/CD**: Run evaluations before each deployment
   ```bash
   # In your CI pipeline
   docker-compose exec app python scripts/create_golden_tests.py << EOF
   4

   EOF
   ```
