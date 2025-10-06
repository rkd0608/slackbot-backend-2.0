-- Create tables for golden test set and evaluation tracking
-- Migration: Create evaluation infrastructure

-- Golden test cases table
CREATE TABLE IF NOT EXISTS golden_tests (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    test_id VARCHAR(100) NOT NULL UNIQUE,
    test_name VARCHAR(255) NOT NULL,
    query TEXT NOT NULL,
    query_type VARCHAR(100),
    expected_message_ids JSON,
    expected_answer TEXT,
    expected_entities JSON,
    expected_channels JSON,
    min_relevance_score FLOAT DEFAULT 0.7,
    required_in_top_k INT DEFAULT 5,
    category VARCHAR(100),
    difficulty VARCHAR(50),
    tags JSON,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_test_id (test_id),
    INDEX idx_category_active (category, is_active),
    INDEX idx_query_type_active (query_type, is_active),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Evaluation runs table
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(100) NOT NULL UNIQUE,
    run_name VARCHAR(255),
    model_version VARCHAR(100),
    retrieval_config JSON,
    total_tests INT DEFAULT 0,
    passed_tests INT DEFAULT 0,
    failed_tests INT DEFAULT 0,
    avg_precision_at_5 FLOAT,
    avg_recall_at_5 FLOAT,
    avg_mrr FLOAT,
    avg_ndcg FLOAT,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    INDEX idx_run_id (run_id),
    INDEX idx_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Evaluation results table
CREATE TABLE IF NOT EXISTS evaluation_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(100) NOT NULL,
    test_id VARCHAR(100) NOT NULL,
    query TEXT NOT NULL,
    retrieved_message_ids JSON,
    retrieval_scores JSON,
    precision_at_5 FLOAT,
    recall_at_5 FLOAT,
    reciprocal_rank FLOAT,
    ndcg_at_5 FLOAT,
    generated_answer TEXT,
    answer_similarity_score FLOAT,
    passed TINYINT DEFAULT 0,
    failure_reason TEXT,
    latency_ms INT,
    evaluated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_run_id (run_id),
    INDEX idx_test_id (test_id),
    INDEX idx_run_test (run_id, test_id),
    INDEX idx_run_passed (run_id, passed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
