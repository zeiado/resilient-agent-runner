# resilient-agent-runner

Backend that runs multi-step agent tasks reliably. The point of the project is reliability
(checkpointing, leases, retries, approval, failover), not AI quality.

## Commands

```bash
docker compose up -d --build        # full stack, API on http://localhost:8080
docker compose run --rm tests       # all tests; needs postgres + redis from compose
docker compose run --rm tests pytest tests/test_recovery.py -q
docker compose logs -f worker       # JSON logs, one object per line
./scripts/demo.sh                   # crash recovery + failover demo
docker compose down -v              # stop and delete data

# optional local model (Ollama in compose, profile local-llm)
docker compose --profile local-llm up -d
docker compose exec ollama ollama pull qwen2.5:3b
LLM_PROVIDER=ollama docker compose up -d worker
```

The `tests` service mounts the working tree, so tests see code changes without a rebuild.
`api1`, `api2` and `worker` do not: rebuild after changing `app/`.

## Layout

- `app/agent.py`: claim, heartbeat, agent loop, step execution and retries. The core of the project.
- `app/reaper.py`: finds stale runs and re-enqueues them.
- `app/main.py`: HTTP API. `app/worker.py`: ARQ settings. `app/tools.py`: the three tools.
- `app/llm.py`: LLM interface, `MockLLM` (default), `build_llm()`. `app/llm_claude.py`: Claude.
  `app/llm_openai.py`: OpenAI-compatible endpoints (`LLM_PROVIDER=ollama`); it reuses the tool
  definitions and system prompt from `llm_claude.py`.
- `alembic/versions/`: migrations. `nginx/nginx.conf`: load balancer.

## Rules

- Stack is fixed: Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, Postgres 16, Redis 7,
  ARQ, Nginx, docker compose, pytest + httpx. Ask before adding a dependency.
- Postgres is the source of truth. Redis jobs are hints; a duplicate or lost job must be harmless.
- Every state change by a worker goes through `owned_run()` in `app/agent.py`, which checks the lease
  under a row lock. Do not write to `runs` or `run_steps` from a worker any other way.
- Run status changes are conditional UPDATEs (`WHERE status = <expected>`). No read-then-write.
- `Tool.run` can be called more than once for the same step, so it must have no side effects. Side
  effects go in `Tool.commit`, which runs in the transaction that marks the step completed, and must be
  idempotent on `(run_id, step_no)`.
- Schema changes need a new Alembic migration and a matching change in `app/models.py`
  (`tests/test_schema.py` compares them).
- Tests run against real Postgres. Do not mock the database, and never weaken or delete a test to make
  it pass.
- `fetch_url` must only reach public addresses. Keep the address check, the pinned connection and the
  per-redirect check in `app/tools.py`; a URL that is blocked raises `NonRetryable`.
- Mock is the default LLM provider, and tests and `scripts/demo.sh` use it. LLM implementations are
  tested with fake clients, never against a live endpoint.
- Keep it plain: no new abstractions or frameworks, no comments that restate the code.
- Every log line is JSON with a `run_id` field (`app/log.py`); set `run_id_var` when entering run context.

## Do not touch without asking

- The claim statement and `owned_run()` in `app/agent.py`: the race-condition guarantees depend on them.
- Unique constraints `uq_run_steps_run_step`, `uq_outbox_run_step` and `runs.idempotency_key`.
- `alembic/versions/0001_initial.py`: add a new migration instead of editing it.
- `max_fails`, `fail_timeout`, `proxy_next_upstream` and `resolve` in `nginx/nginx.conf`.
- The default timings (heartbeat 10 s, stale 60 s, reaper 30 s, tool timeout 30 s, 3 attempts, 10 steps).
