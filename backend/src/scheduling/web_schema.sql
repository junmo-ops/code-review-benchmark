CREATE TABLE IF NOT EXISTS bench_web_state (
    scope VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    value JSON NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    owner CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_until DATETIME(6) NULL,
    PRIMARY KEY(scope, name)
) ENGINE=InnoDB;
