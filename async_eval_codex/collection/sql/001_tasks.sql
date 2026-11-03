-- 请手动在现有 checkpointer 使用的 PostgreSQL 数据库中执行此脚本。
CREATE TABLE IF NOT EXISTS agent_eval_task (
    run_id TEXT PRIMARY KEY,
    snapshot JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    evaluation JSONB,
    reflection JSONB,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS agent_eval_task_queue ON agent_eval_task (status, available_at);
