-- Add enhanced metadata columns to query_logs table
-- Migration: Add retrieval metadata and query enhancement tracking

ALTER TABLE query_logs
ADD COLUMN retrieval_scores JSON NULL AFTER result_message_ids,
ADD COLUMN retrieval_sources JSON NULL AFTER retrieval_scores,
ADD COLUMN top_result_score FLOAT NULL AFTER retrieval_sources,
ADD COLUMN avg_result_score FLOAT NULL AFTER top_result_score,
ADD COLUMN original_query TEXT NULL AFTER query_text,
ADD COLUMN rewritten_query TEXT NULL AFTER original_query,
ADD COLUMN expanded_queries JSON NULL AFTER rewritten_query,
ADD COLUMN query_was_rewritten TINYINT DEFAULT 0 AFTER expanded_queries,
ADD COLUMN query_was_expanded TINYINT DEFAULT 0 AFTER query_was_rewritten;
