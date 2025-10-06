# Grafana Setup Guide

Complete guide for setting up and using Grafana dashboards for AI Quality monitoring.

## Quick Start

### 1. Start Grafana

Grafana is already added to your docker-compose:

```bash
docker-compose up -d grafana
```

### 2. Access Grafana

Open http://localhost:3000 in your browser

**Default credentials:**
- Username: `admin`
- Password: `admin`

(You'll be prompted to change the password on first login)

### 3. Dashboard Already Provisioned

The **AI Quality Assurance Dashboard** is automatically loaded from `grafana/dashboards/ai_quality_dashboard.json`

Navigate to: **Dashboards** → **AI Quality Assurance Dashboard**

## Dashboard Panels

### 📊 **Top Row - Key Metrics**

**1. Evaluation Pass Rate (Gauge)**
- Shows % of golden tests that passed in latest evaluation
- Green (≥80%), Yellow (≥60%), Red (<60%)
- Updates when you run evaluations

**2. Feedback Rate (Gauge)**
- % of queries users gave feedback on (last 24h)
- Green (≥40%), Yellow (≥20%), Red (<20%)
- Goal: Get users to rate more responses

**3. Feedback Rate Over Time (Line Chart)**
- Thumbs up vs thumbs down trends
- Refreshes every 10 seconds
- Shows 5-minute rate

### 📈 **Middle Row - Retrieval Quality**

**4. Average Retrieval Score (Line Chart)**
- Top result relevance scores over time
- Three time windows: 1h, 24h, 7d
- Range: 0-1 (higher is better)
- Goal: Keep above 0.6

**5. Queries with No Results (Line Chart)**
- Queries that returned zero results by type
- Broken down by query_type (factual, summary, etc.)
- Should trend downward as you improve

### 🔍 **Bottom Row - Deep Dive**

**6. Evaluation Metrics (Bar Chart)**
- Latest run's Precision@5, Recall@5, MRR, NDCG@5
- All metrics range 0-1
- Updated when you run evaluations

**7. Query Enhancement Rates (Line Chart)**
- % queries rewritten (conversation context)
- % queries expanded (synonyms)
- Based on last 24h

**8. Low Confidence Retrievals (Line Chart)**
- Queries with top score below thresholds (0.3, 0.5, 0.7)
- Watch for spikes indicating quality issues

## Using the Dashboard

### Monitor Quality in Real-Time

1. **Open dashboard** - Auto-refreshes every 10 seconds
2. **Watch feedback rate** - Aim for >30% feedback collection
3. **Check retrieval scores** - Should stay above 0.6
4. **Look for anomalies** - Sudden drops = investigate

### After Running Evaluations

```bash
# Run evaluation
docker-compose exec app python scripts/create_golden_tests.py
# Select option 4

# Dashboard automatically updates with:
# - New pass rate
# - Latest Precision/Recall/MRR/NDCG
```

### Investigating Issues

**If pass rate drops:**
1. Check which category failed (run evaluation with filters)
2. Look at failing tests in database
3. Review retrieval scores for those queries

**If feedback rate is low:**
1. Verify feedback buttons are showing (check Slack app)
2. Ensure interactivity endpoint is configured
3. Remind users to rate responses

**If "no results" spikes:**
1. Check query types affected
2. Verify embeddings are current
3. Look for new query patterns users are trying

## Customizing Dashboards

### Add New Panel

1. Click **Add** → **Visualization**
2. Select **Prometheus** datasource
3. Enter PromQL query:
   ```promql
   # Example: Query latency
   rate(query_latency_seconds_sum[5m]) / rate(query_latency_seconds_count[5m])
   ```
4. Configure visualization type
5. Save dashboard

### Useful Queries

```promql
# Total queries per minute
rate(query_requests_total[1m])

# Average top result score (1 hour)
avg_retrieval_score{time_window="1h"}

# Feedback breakdown
sum by(feedback_type) (rate(feedback_responses_total[5m]))

# Low confidence query rate
rate(retrieval_low_confidence_total{threshold="0.5"}[5m])

# Evaluation pass rate
evaluation_pass_rate{category="overall"}

# Latest Precision@5
evaluation_precision_at_5{category="overall"}
```

### Create Alerts

1. **Edit panel** → **Alert** tab
2. **Create alert rule**:
   ```
   WHEN evaluation_pass_rate < 70
   FOR 5m
   ```
3. **Add notification channel** (Slack, Email, etc.)
4. Test alert

## Best Practices

### Daily Monitoring

- [ ] Check feedback rate (goal: >30%)
- [ ] Monitor avg retrieval score (goal: >0.6)
- [ ] Review no-results count (should be minimal)

### Weekly

- [ ] Run evaluation and compare to baseline
- [ ] Review failing tests
- [ ] Check for new query patterns

### Before Deployment

- [ ] Run full evaluation
- [ ] Pass rate should be ≥80%
- [ ] No significant metric regressions

## Metrics Reference

All metrics exposed at `http://localhost:8000/metrics`

### Quality Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `feedback_responses_total` | Counter | Thumbs up/down count |
| `feedback_rate` | Gauge | % queries with feedback (24h) |
| `avg_retrieval_score{time_window}` | Gauge | Avg top result score |
| `retrieval_no_results_total` | Counter | Zero-result queries |
| `retrieval_low_confidence_total` | Counter | Below threshold queries |
| `query_rewrite_rate` | Gauge | % queries rewritten |
| `query_expansion_rate` | Gauge | % queries expanded |
| `evaluation_pass_rate{category}` | Gauge | Golden test pass rate |
| `evaluation_precision_at_5` | Gauge | Precision@5 |
| `evaluation_recall_at_5` | Gauge | Recall@5 |
| `evaluation_mrr` | Gauge | Mean Reciprocal Rank |
| `evaluation_ndcg_at_5` | Gauge | NDCG@5 |

## Troubleshooting

### Dashboard shows "No data"

**Check Prometheus datasource:**
```bash
# Test metrics endpoint
curl http://localhost:8000/metrics | grep evaluation

# Check if metrics updater is running
docker-compose logs app | grep metrics_updater
```

**Fix:**
1. Restart app: `docker-compose restart app`
2. Wait 60 seconds (metrics update every minute)
3. Refresh Grafana dashboard

### Datasource connection error

The datasource URL should be: `http://host.docker.internal:8000/metrics`

**On Linux**, change to: `http://172.17.0.1:8000/metrics`

Edit: **Configuration** → **Data Sources** → **Prometheus** → **URL**

### Panels show old data

1. Check time range (top right) - ensure it includes recent data
2. Click refresh icon (top right)
3. Verify metrics are updating: `curl http://localhost:8000/metrics`

## Advanced: Creating Custom Dashboards

### Example: Query Type Analysis

```json
{
  "targets": [{
    "expr": "sum by(intent_type) (rate(query_requests_total[5m]))",
    "legendFormat": "{{intent_type}}"
  }],
  "title": "Queries by Type"
}
```

### Example: Hourly Quality Heatmap

```json
{
  "targets": [{
    "expr": "avg_retrieval_score{time_window=\"1h\"}",
    "format": "time_series"
  }],
  "type": "heatmap"
}
```

## Next Steps

1. **Set up alerts** for critical metrics
2. **Create team dashboards** for different stakeholders
3. **Export dashboards** for backup
4. **Integrate with CI/CD** to block deploys on quality regressions

---

**Need help?** Check the metrics endpoint: http://localhost:8000/metrics
