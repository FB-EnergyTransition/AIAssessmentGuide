CREATE TABLE testsuite.test_ratings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES testsuite.test_runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    data JSONB NOT NULL,
    FOREIGN KEY (run_id, case_id) REFERENCES testsuite.test_cases(run_id, id) ON DELETE CASCADE
);
CREATE INDEX test_ratings_run_idx ON testsuite.test_ratings(run_id);
