"""基于 PostgreSQL 的任务队列，通过租约和令牌保护写入；运行时不修改表结构。"""
import json
from uuid import uuid4


class Repository:
    def __init__(self, pool):
        self.pool = pool

    async def submit(self, snapshot):
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent_eval_task (run_id, snapshot) VALUES (%s, %s::jsonb) "
                "ON CONFLICT (run_id) DO NOTHING",
                (snapshot.run_id, snapshot.model_dump_json()),
            )

    async def claim(self, lease_seconds=180, max_attempts=3):
        token = str(uuid4())
        async with self.pool.connection() as conn:
            # 达到重试上限的过期任务应标记失败，避免进程崩溃后一直处于运行状态。
            await conn.execute(
                "UPDATE agent_eval_task SET status='failed', lease_until=NULL "
                "WHERE status='running' AND lease_until < now() AND attempts >= %s",
                (max_attempts,),
            )
            cursor = await conn.execute("""
                WITH next_task AS (
                    SELECT run_id FROM agent_eval_task
                    WHERE attempts < %s AND (
                        (status IN ('pending', 'retry_wait') AND available_at <= now())
                        OR (status='running' AND lease_until < now()))
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE agent_eval_task t SET status='running', attempts=attempts+1,
                    lease_token=%s, lease_until=now() + %s * interval '1 second'
                FROM next_task n WHERE t.run_id=n.run_id
                RETURNING t.run_id, t.snapshot, t.evaluation, t.attempts
            """, (max_attempts, token, lease_seconds))
            row = await cursor.fetchone()
            return (*row, token) if row else None

    async def save_evaluation(self, run_id, token, evaluation):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent_eval_task SET evaluation=%s::jsonb WHERE run_id=%s "
                "AND lease_token=%s AND status='running' AND lease_until > now()",
                (json.dumps(evaluation), run_id, token),
            )
            return cursor.rowcount == 1

    async def finish(self, run_id, token, reflection):
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE agent_eval_task SET status='succeeded', reflection=%s::jsonb, "
                "lease_until=NULL, last_error=NULL, finished_at=now() "
                "WHERE run_id=%s AND lease_token=%s AND status='running' AND lease_until > now()",
                (json.dumps(reflection), run_id, token),
            )

    async def fail(self, run_id, token, attempts, error, max_attempts=3):
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE agent_eval_task SET status=%s, last_error=%s, lease_until=NULL, "
                "available_at=now() + %s * interval '1 second' "
                "WHERE run_id=%s AND lease_token=%s AND status='running' AND lease_until > now()",
                ("failed" if attempts >= max_attempts else "retry_wait", error,
                 min(60, 2 ** attempts), run_id, token),
            )

    async def get(self, run_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT status, evaluation, reflection, last_error, attempts "
                "FROM agent_eval_task WHERE run_id=%s", (run_id,),
            )
            row = await cursor.fetchone()
            return dict(zip(("status", "evaluation", "reflection", "last_error", "attempts"), row)) if row else None

    async def save_reflection(self, run_id, token, reflection):
        """生成改进方案前保存反思，重试时不重复分析同一执行。"""
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent_eval_task SET reflection=%s::jsonb WHERE run_id=%s "
                "AND lease_token=%s AND status='running' AND lease_until > now()",
                (json.dumps(reflection), run_id, token),
            )
            return cursor.rowcount == 1

    async def get_snapshot(self, run_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT snapshot FROM agent_eval_task WHERE run_id=%s", (run_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_proposal(self, proposal_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload, status, regression, review FROM agent_eval_proposal WHERE proposal_id=%s",
                (proposal_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return {**row[0], "status": row[1], "regression": row[2], "review": row[3]}

    async def list_proposals(self, run_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT proposal_id, status, payload->>'target', payload->>'kind' "
                "FROM agent_eval_proposal WHERE run_id=%s ORDER BY created_at, proposal_id", (run_id,)
            )
            return [dict(zip(("proposal_id", "status", "target", "kind"), row))
                    for row in await cursor.fetchall()]

    async def save_proposal(self, proposal, lease_token=None):
        async with self.pool.connection() as conn:
            if lease_token is not None:
                # 与任务结束使用相同的行锁，避免失去租约的进程写入候选。
                cursor = await conn.execute(
                    "SELECT run_id FROM agent_eval_task WHERE run_id=%s AND lease_token=%s "
                    "AND status='running' AND lease_until > now() FOR UPDATE",
                    (proposal["run_id"], lease_token),
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("任务租约已失效，无法保存改进候选")
            await conn.execute(
                "INSERT INTO agent_eval_proposal (proposal_id, run_id, payload, status) "
                "VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (proposal_id) DO NOTHING",
                (proposal["proposal_id"], proposal["run_id"], json.dumps(proposal), proposal["status"]),
            )
        return await self.get_proposal(proposal["proposal_id"])

    async def save_regression(self, proposal_id, report):
        async with self.pool.connection() as conn:
            # 已审核的候选不可悄悄替换其验证依据。
            cursor = await conn.execute(
                "UPDATE agent_eval_proposal SET regression=%s::jsonb, updated_at=now() "
                "WHERE proposal_id=%s AND status='pending_review' "
                "AND payload->>'base_hash'=%s AND payload->>'candidate_hash'=%s",
                (json.dumps(report), proposal_id, report["base_hash"], report["candidate_hash"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("候选不存在、版本不匹配或已审核，无法保存回归报告")

    async def review_proposal(self, proposal_id, decision, current_prompt, reviewer, note):
        from ..reflection.improvement import validate_review
        if decision not in {"approved", "rejected"} or not reviewer.strip() or not note.strip():
            raise ValueError("请提供有效的审核决定、审核人和审核说明")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT payload, regression, status FROM agent_eval_proposal "
                "WHERE proposal_id=%s FOR UPDATE", (proposal_id,),
            )
            row = await cursor.fetchone()
            if row is None or row[2] != "pending_review":
                raise ValueError("候选不存在或当前状态不允许审核")
            if decision == "approved":
                validate_review(row[0], row[1], current_prompt)
            await conn.execute(
                "UPDATE agent_eval_proposal SET status=%s, review=%s::jsonb, updated_at=now() "
                "WHERE proposal_id=%s",
                (decision, json.dumps({"reviewer": reviewer, "note": note}), proposal_id),
            )
