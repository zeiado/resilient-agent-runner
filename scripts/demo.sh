#!/usr/bin/env bash
# End-to-end demo: crash recovery of a run, then API failover behind Nginx.
set -euo pipefail
cd "$(dirname "$0")/.."

API=http://localhost:8080

# Short timings so the demo doesn't sit idle. Production defaults are 10s / 60s / 30s.
export MOCK_LLM_DELAY_SECONDS=4
export HEARTBEAT_INTERVAL_SECONDS=2
export HEARTBEAT_STALE_SECONDS=10
export REAPER_INTERVAL_SECONDS=5

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

show_run() {
  curl -s "$API/runs/$RUN_ID" | python3 -c '
import json, sys
run = json.load(sys.stdin)
print("  run status:", run["status"], "| error:", run["error"])
for s in run["steps"]:
    print("    step %d  %-10s %-17s attempts=%d" % (s["step_no"], s["tool"], s["status"], s["attempts"]))'
}

run_field() { curl -s "$API/runs/$RUN_ID" | python3 -c "import json,sys; r=json.load(sys.stdin); print($1)"; }

wait_until() {  # wait_until <python expression over r> <timeout seconds>
  for _ in $(seq 1 "$2"); do
    [ "$(run_field "$1")" = "True" ] && return 0
    sleep 1
  done
  echo "timed out waiting for: $1"; show_run; exit 1
}

say "1. Start the stack (postgres, redis, 2 API replicas, worker, nginx)"
docker compose up -d --build --quiet-pull 2>&1 | grep -E 'Started|Healthy|Exited' || true
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' $API/health)" = "200" ] && break
  sleep 1
done
curl -s $API/health; echo

say "2. Create a run (same idempotency key sent twice returns the same run)"
KEY="demo-$(date +%s)"
BODY="{\"task\": \"Summarize http://nginx/health and email it\", \"idempotency_key\": \"$KEY\"}"
RUN_ID=$(curl -s -X POST $API/runs -H 'content-type: application/json' -d "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
AGAIN=$(curl -s -X POST $API/runs -H 'content-type: application/json' -d "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "  first POST  -> $RUN_ID"
echo "  second POST -> $AGAIN"

say "3. Wait for step 1 to be checkpointed, then kill the worker mid-run (SIGKILL)"
wait_until 'len(r["steps"]) >= 1 and r["steps"][0]["status"] == "completed"' 30
docker compose kill worker 2>&1 | tail -1
show_run
echo "  (the run is stuck in 'running': its owner is dead and the heartbeat has stopped)"

say "4. Restart the worker; the reaper finds the stale heartbeat and the run resumes"
docker compose up -d worker 2>&1 | tail -1
wait_until 'r["status"] == "awaiting_approval"' 60
show_run
echo "  step 1 still has attempts=1: it was not executed again"
docker compose logs worker --since 60s 2>/dev/null | grep -E 'stale run re-enqueued|run claimed' | tail -2 | sed 's/^/  /'

say "5. The run is paused before send_email; approve it"
curl -s -X POST "$API/runs/$RUN_ID/approve" -o /dev/null -w '  POST /approve -> %{http_code}\n'
wait_until 'r["status"] == "completed"' 60
show_run
echo "  rows in outbox for this run:" \
  "$(docker compose exec -T postgres psql -U runner -d runner -tAc "SELECT count(*) FROM outbox WHERE run_id = '$RUN_ID'")"

say "6. Stop one API replica; requests through Nginx keep succeeding"
docker compose stop api1 2>&1 | tail -1
for _ in $(seq 1 20); do
  curl -s -o /dev/null -w '%{http_code}\n' "$API/runs/$RUN_ID"
done | sort | uniq -c | sed 's/^/  responses: /'
curl -s -o /dev/null -D - $API/health | grep -iE 'HTTP|x-upstream' | sed 's/^/  /'

say "7. Bring the replica back"
docker compose start api1 2>&1 | tail -1
echo "Done. Run 'docker compose down -v' to remove everything."
