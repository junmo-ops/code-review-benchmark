CREATE TABLE IF NOT EXISTS bench_scheduler_scopes (
    scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    global_limit INT UNSIGNED NOT NULL,
    worker_limit INT UNSIGNED NOT NULL,
    paused BOOLEAN NOT NULL DEFAULT FALSE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bench_scheduler_tasks (
    scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    task_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    case_id VARCHAR(512) NOT NULL,
    pool VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload JSON NOT NULL,
    payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'queued',
    owner VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    generation INT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts INT UNSIGNED NOT NULL,
    lease_until DATETIME(6) NULL,
    checkpoint JSON NULL,
    result JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (scope, task_id),
    INDEX claim_tasks (scope, status, created_at),
    INDEX lease_tasks (scope, status, lease_until),
    INDEX run_tasks (scope, run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bench_scheduler_pools (
    scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pool VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    concurrency_limit INT UNSIGNED NOT NULL,
    PRIMARY KEY (scope, pool)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bench_scheduler_attempts (
    scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    task_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    generation INT UNSIGNED NOT NULL,
    owner VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    ended_at DATETIME(6) NULL,
    checkpoint JSON NULL,
    result JSON NULL,
    PRIMARY KEY (scope, task_id, generation)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bench_scheduler_runs (
 scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 run_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 job JSON NOT NULL,
 plan JSON NOT NULL,
 prepared JSON NULL,
 result JSON NULL,
 aggregate_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
 status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY(scope,run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bench_scheduler_workers (
 scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 owner VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 seen DATETIME(6) NOT NULL,
 PRIMARY KEY(scope,owner)
) ENGINE=InnoDB;
