# Pinecone Namespace Migration Plan
## Moving from Global to Team-Specific Namespaces

## Current Architecture (Single Tenant)

```
slack-embeddings index:
├── __default__ namespace
│   └── All team message embeddings mixed together
└── code namespace
    └── All team code snippet embeddings mixed together
```

**Problems:**
- ❌ No team isolation (privacy/security issue)
- ❌ Slower queries (searching across ALL teams)
- ❌ Can't delete a single team's data
- ❌ Inconsistent with GitHub integration

## Target Architecture (Multi-Tenant)

```
slack-embeddings index:
├── {team_id}:messages namespace
│   └── Team-specific message embeddings
├── {team_id}:code namespace
│   └── Team-specific code snippet embeddings
├── {team_id}:files namespace
│   └── Team-specific file embeddings
└── (repeat for each team)

github-code-embeddings index:
├── {team_id}:code namespace
│   └── GitHub code structure embeddings
└── {team_id}:semantic namespace
    └── GitHub semantic embeddings
```

**Benefits:**
- ✅ Full team isolation (security & privacy)
- ✅ 10-100x faster queries (smaller search space)
- ✅ Easy team data deletion
- ✅ Consistent naming across all integrations
- ✅ Better scalability

## Migration Strategy

### Phase 1: Add Namespace Support (No Breaking Changes)

**Goal:** Make code namespace-aware without breaking existing functionality

**Changes:**
1. Update `VectorDBManager` to use team-specific namespaces
2. Update `EmbeddingService` to write to `{team_id}:messages`
3. Update `CodeEmbeddingService` to write to `{team_id}:code`
4. Update retrieval services to query from team-specific namespaces
5. Keep backward compatibility - read from old namespaces if team namespace is empty

**Code Changes:**
- `app/core/vector_db.py` - Add helper methods for namespace generation
- `app/services/embedding_service.py` - Use team namespace
- `app/services/code_embedding_service.py` - Use team namespace
- `app/services/retrieval_service.py` - Query team namespace

### Phase 2: Migrate Existing Data

**Goal:** Move data from global to team-specific namespaces

**Approach:**
1. Create migration script that:
   - Queries all vectors from `__default__` namespace
   - Groups by `team_id` from metadata
   - Re-upserts to `{team_id}:messages` namespace
   - Deletes from `__default__` after verification
2. Repeat for `code` namespace → `{team_id}:code`

**Migration Script:** `scripts/migrate_pinecone_namespaces.py`

### Phase 3: Remove Fallback Logic

**Goal:** Clean up backward compatibility code

**Changes:**
- Remove fallback to old namespaces
- Update documentation
- Monitoring & alerting

## Rollback Plan

If migration fails:
1. Revert code changes
2. Keep new namespaced data as backup
3. Continue using old namespaces
4. Old data is never deleted until verification

## Namespace Naming Convention

### Standard Format:
```
{team_id}:{content_type}
```

### Examples:
- `T0721T4PN4U:messages` - Slack messages
- `T0721T4PN4U:code` - Slack code snippets
- `T0721T4PN4U:files` - Slack file embeddings
- `T0721T4PN4U:semantic` - GitHub semantic embeddings (GitHub only)

### Content Types:
- `messages` - Regular Slack messages
- `code` - Code snippets (Slack) or code structure (GitHub)
- `files` - File embeddings (PDFs, docs, etc.)
- `semantic` - Semantic embeddings (GitHub only - dual embedding strategy)

## Performance Impact

### Query Performance:
**Before (global namespace):**
```
10,000 teams × 10,000 messages = 100M vectors to search
Query time: ~500-1000ms
```

**After (team namespace):**
```
1 team × 10,000 messages = 10K vectors to search
Query time: ~5-10ms (100x faster!)
```

### Storage:
- No change - same number of vectors
- Better organized by namespace

## Implementation Timeline

### Week 1: Code Changes
- Day 1-2: Update VectorDBManager with namespace helpers
- Day 3-4: Update embedding services
- Day 5: Update retrieval services
- Day 6-7: Testing & QA

### Week 2: Migration
- Day 1: Backup current Pinecone data
- Day 2-3: Run migration script (can run in background)
- Day 4: Verification
- Day 5: Monitor & fix issues

### Week 3: Cleanup
- Remove fallback logic
- Documentation
- Performance monitoring

## Risk Assessment

**Low Risk:**
- ✅ Backward compatible during migration
- ✅ Can rollback easily
- ✅ No data loss (old data kept until verified)

**Medium Risk:**
- ⚠️ Increased Pinecone API calls during migration
- ⚠️ Need to test with multiple teams

**Mitigation:**
- Run migration during low-traffic hours
- Rate limit migration script
- Extensive testing before production

## Next Steps

1. ✅ Review this plan
2. → Implement Phase 1 (namespace support)
3. → Create migration script
4. → Test with staging environment
5. → Run migration in production
6. → Monitor & cleanup
