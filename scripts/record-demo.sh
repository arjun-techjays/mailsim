#!/usr/bin/env bash
# Records the invoice bank-change demo (docs/demo.md) from real runs into one directory:
# a passing baseline build, a regressed candidate build, the CI gate and comparison, and each
# run's audit log, agent-sent mail and evaluation reports (fetched through the operator API).
#
#   scripts/record-demo.sh [OUT_DIR]          # default var/demo
#
# Uses a fresh database `mailsim_demo` on the Compose PostgreSQL (dropped and recreated) and
# the LLM provider in MAILSIM_LLM_PROVIDER (default: the offline stub, which grades the
# semantic assertion INCONCLUSIVE). docs/demo/recorded/ was made with
# MAILSIM_LLM_PROVIDER=claude_cli. Exits non-zero unless the baseline passes, the candidate
# fails and `sim bench compare` reports the regression.
set -euo pipefail

OUT=${1:-var/demo}
PG=${MAILSIM_DEMO_PG:-postgresql+psycopg://mailsim:mailsim@localhost:54329}
PORT=${MAILSIM_DEMO_PORT:-18090}
PACK=scenarios/finance.invoice-bank-change
BENCH=(--rounds 2 --advance-seconds 600)
WORK=$(mktemp -d)
trap 'kill "${SERVER:-}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

export MAILSIM_DATABASE_URL="$PG/mailsim_demo"
export MAILSIM_BLOB_DIR="$WORK/blobs"
export PYTHONPATH=.

uv run python - "$PG/mailsim" <<'EOF'
import sys
from sqlalchemy import create_engine, text
with create_engine(sys.argv[1], isolation_level="AUTOCOMMIT").connect() as c:
    c.execute(text('DROP DATABASE IF EXISTS "mailsim_demo" WITH (FORCE)'))
    c.execute(text('CREATE DATABASE "mailsim_demo"'))
EOF
uv run python -m mailsim.serve bootstrap --migrate --output "$WORK/keys.json" > /dev/null
key() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$WORK/keys.json" "$1"; }
PROJECT=$(key project_id)
EVALUATOR=$(key evaluator_key)

rm -rf "$OUT" && mkdir -p "$OUT"
cp "$PACK/scenario.yaml" "$PACK/answer_key.yaml" "$OUT/"

# 1. The baseline build passes the gate; 2. the candidate build fails it.
for build in careful naive; do
  set +e
  uv run sim bench run "$PACK" --project "$PROJECT" --build "$build" \
    --agent-callable "examples.finance_agents:$build" "${BENCH[@]}" \
    --json "$OUT/bench-$build.json" --junit "$OUT/bench-$build.xml" > /dev/null
  echo $? > "$OUT/bench-$build.exit"
  set -e
done
# 3. CI compares the candidate with the baseline.
set +e
uv run sim bench compare "$OUT/bench-careful.json" "$OUT/bench-naive.json" > "$OUT/compare.json"
echo $? > "$OUT/compare.exit"
set -e

# 4. Per build: the audit log, the mail the agent sent, and the evaluation reports.
uv run python -m mailsim.serve serve --host 127.0.0.1 --port "$PORT" --no-protocols \
  > "$WORK/serve.log" 2>&1 &
SERVER=$!
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/v1/health" > /dev/null 2>&1 && break
  sleep 1
done
for build in careful naive; do
  read -r RUN EVAL < <(python3 -c '
import json, sys
t = json.load(open(sys.argv[1]))["scenarios"][0]["trials"][0]
print(t["run_id"], t["evaluation_id"])' "$OUT/bench-$build.json")
  uv run sim run-actions "$RUN" --project "$PROJECT" > "$OUT/actions-$build.json"
  uv run sim run-state "$RUN" --project "$PROJECT" | python3 -c '
import json, sys
s = json.load(sys.stdin)
sent = {f["id"] for f in s["folders"] if f["system_type"] == "sent"}
print(json.dumps([{"sent_at": m["created_at"], "to": [a["email"] for a in m["to"]],
                   "subject": m["subject"], "body": m["body_text"].strip()}
                  for m in s["messages"]
                  if m["folder_id"] in sent and m["from"]["email"] == "finance@example.test"],
                 indent=2))' > "$OUT/sent-$build.json"
  for fmt in markdown json; do
    ext=$([ "$fmt" = markdown ] && echo md || echo json)
    curl -fsS -H "Authorization: Bearer $EVALUATOR" \
      "http://127.0.0.1:$PORT/v1/runs/$RUN/evaluations/$EVAL/report?format=$fmt" \
      > "$OUT/report-$build.$ext"
  done
done

B=$(cat "$OUT/bench-careful.exit") N=$(cat "$OUT/bench-naive.exit") C=$(cat "$OUT/compare.exit")
echo "baseline (careful) exit $B, candidate (naive) exit $N, compare exit $C -> $OUT"
[ "$B" = 0 ] && [ "$N" = 1 ] && [ "$C" = 1 ]
