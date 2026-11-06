CREATE TABLE testsuite.test_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE CHECK (id = run_id),
    data JSONB NOT NULL
);
CREATE TABLE testsuite.test_cases (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES testsuite.test_runs(id) ON DELETE CASCADE,
    data JSONB NOT NULL,
    UNIQUE (run_id, id)
);
CREATE TABLE testsuite.test_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES testsuite.test_runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    data JSONB NOT NULL,
    FOREIGN KEY (run_id, case_id) REFERENCES testsuite.test_cases(run_id, id) ON DELETE CASCADE
);
CREATE TABLE testsuite.test_assertions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES testsuite.test_runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    data JSONB NOT NULL,
    FOREIGN KEY (run_id, case_id) REFERENCES testsuite.test_cases(run_id, id) ON DELETE CASCADE
);
CREATE TABLE testsuite.test_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES testsuite.test_runs(id) ON DELETE CASCADE,
    case_id TEXT,
    data JSONB NOT NULL,
    FOREIGN KEY (run_id, case_id) REFERENCES testsuite.test_cases(run_id, id) ON DELETE CASCADE
);
CREATE INDEX test_cases_run_idx ON testsuite.test_cases(run_id);
CREATE INDEX test_events_run_idx ON testsuite.test_events(run_id);
CREATE INDEX test_assertions_run_idx ON testsuite.test_assertions(run_id);
CREATE INDEX test_artifacts_run_idx ON testsuite.test_artifacts(run_id);
CREATE INDEX test_runs_finished_idx ON testsuite.test_runs((data->>'finished_at'));
