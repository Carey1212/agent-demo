-- 启用 AGENT_IMPROVEMENT_ENABLED 前手动执行，不修改已有任务字段。
CREATE TABLE IF NOT EXISTS agent_eval_proposal (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_eval_task(run_id),
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'needs_investigation', 'approved', 'rejected')),
    regression JSONB,
    review JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_eval_proposal_run ON agent_eval_proposal (run_id);
