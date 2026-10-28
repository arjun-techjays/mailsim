# CI regression suite and build comparison (`sim bench`)

Spec §9.4 and the release checklist items "Export machine-readable results for CI" and
"Compare results between two agent builds". Two commands:

* `sim bench run` runs a fixed-fixture suite against one agent build, with repeated trials,
  and gates on the pass rate.
* `sim bench compare` compares two `bench run` results and fails when the candidate build
  regressed. It does not need a database.

## `sim bench run`

```bash
export MAILSIM_DATABASE_URL=postgresql+psycopg://mailsim:mailsim@localhost:54329/mailsim
uv run sim bench run scenarios/finance.invoice-bank-change scenarios/leadership \
  --build "$GIT_SHA" --agent-cmd "python -m my_agent" --agent-model "anthropic/claude-x" \
  --trials 5 --rounds 2 --advance-seconds 600 --min-pass-rate 0.8 \
  --json var/bench/$GIT_SHA.json --junit var/bench/$GIT_SHA.xml
```

**Suite.** Each argument is a scenario file, a pack directory (`scenario.yaml|yml|json`, plus
`answer_key.yaml` if it has one), or a directory that is searched recursively for packs (in
sorted path order). Every pack is published into the project (`--project`, else
`MAILSIM_LOCAL_PROJECT_ID`, else the only project). Publishing is idempotent: an unchanged
pack reuses its immutable scenario and answer-key versions. A pack that changed without a
version bump is refused (exit 2). Each trial is graded with the pack's own answer-key version
(or, when the pack has no key file, the latest compatible key, e.g. from inline assertions).

**One trial:** create a run tagged with `--build` (`agent_build`), start it, then for each
of `--rounds`: start the agent, then advance virtual time by `--advance-seconds` (scheduled
events fire and personas reply at their due times). Next, evaluate the recorded run, which
completes it as PASSED or FAILED, and revoke the trial's agent keys. If the run is still live
afterwards (for example after an INCONCLUSIVE evaluation), it is cancelled.

**Agent errors.** A non-zero exit, a timeout or an exception is an agent error. Later rounds
are skipped, the run ends as `ERROR`, and the trial counts as not passed (outcome `ERROR`). The
run is still graded, so the report shows what the agent did before it failed.

### Agent runners

| Option | How the agent runs | Interface it uses |
|---|---|---|
| `--agent-cmd "CMD"` | Subprocess: split with `shlex`, no shell, own process group, killed on `--timeout` (default 600 s per round). | Any agent interface, with `MAILSIM_API_KEY` |
| `--agent-callable pkg.mod:fn` | In-process Python call `fn(ctx)`. `ctx.mailbox` is the run's agent mailbox facade, and `ctx.env` holds the variables below. It may return a usage dict. No timeout. | Facade (actions recorded with interface `api`) |

The agent is given these variables (`--agent-cmd`: in its environment; callable: in `ctx.env`):

| Variable | Meaning |
|---|---|
| `MAILSIM_RUN_ID`, `MAILSIM_MAILBOX_ID`, `MAILSIM_MAILBOX_ADDRESS` | The agent's run and mailbox |
| `MAILSIM_API_KEY` | An agent key bound to that run and mailbox (`--agent-cmd` only), revoked after the trial |
| `MAILSIM_API_URL` | The value of `--api-url` (a running `serve`), if given |
| `MAILSIM_AGENT_BUILD`, `MAILSIM_BENCH_TRIAL`, `MAILSIM_BENCH_ROUND` | Bench bookkeeping |
| `MAILSIM_AGENT_REPORT` | A file the agent may write a JSON object of its own model usage to (`model`, `input_tokens`, `output_tokens`, `cost_usd`, ...). Only scalar values are kept, and they are recorded as reported, without verification. |

The command's environment is deliberately minimal: `PATH`, `HOME`, locale and temp-dir
variables, `PYTHONPATH`, the variables above, and only the extra variables you name with
`--pass-env VAR`. Operator secrets are not inherited, and that includes
`MAILSIM_DATABASE_URL`, which would let an agent read its own answer key. Choose how the agent
reaches its mailbox:

* **REST, Gmail, Graph, MCP over HTTP, IMAP or SMTP:** run `serve` on the same database and
  pass `--api-url http://127.0.0.1:8000`. This is the recommended setup for untrusted agents.
* **MCP stdio:** the agent starts `python -m mailsim.serve mcp-stdio` itself with
  `MAILSIM_API_KEY`. This needs `--pass-env MAILSIM_DATABASE_URL`, which gives the agent
  process database access, so use it only for trusted agents.

The agent's stdout and stderr are captured, never mixed into the bench's JSON. The last 4000
characters of each are kept per round, with the agent key masked.

### What is recorded (spec 9.4)

The result document (`schema: mailsim.bench/1`), printed on stdout and written to `--json`:

* **Run level:** `build` (`id`, `agent_model`, runner type and command), `simulator`
  (`version` and the compatibility profile of each interface), `llm` (persona/judge gateway
  provider and model), `config` (trials, rounds, advance, timeout, checks, thresholds),
  `started_at`, `duration_seconds`.
* **Per scenario:** `scenario_key`, immutable `scenario_version` and `scenario_version_id`,
  fixture `spec_hash`, `answer_key_version` and hash, `stats`, `gate`.
* **Per trial:** `run_id` and `evaluation_id`. The full event log, final mailbox state,
  recorded model outputs and assertion evidence are stored under these ids
  (`sim run-events`, `sim run-state`, the evaluation report endpoints). Also recorded:
  `outcome`, `verdict`, final `run_status`, `seed`, final `state_hash`, `event_count`,
  per-assertion status, `accuracy`, wall `duration_seconds`, `virtual_duration_seconds`,
  `agent_actions`, `denied_actions`, `replies_sent`, the agent `interfaces` used,
  `model_usage` (persona and judge tokens and models, gateway call counts, the agent's
  self-reported usage), and each round's exit code, error and output tails.

**Statistics** (per scenario, from the evaluator's `trial_stats`): `runs`, `pass_rate`,
`mean_accuracy`, `variance` and `stdev` of accuracy (sample, n-1; `null` for one trial),
`pass_variance` (sample variance of pass/fail), `per_assertion_pass_rate`,
`mean_duration_seconds` and `mean_agent_actions`. `summary` aggregates all trials.

**JUnit** (`--junit`): one `<testsuite>` per scenario, one `<testcase>` per trial. A trial
that did not pass is a `<failure>` (with the failed assertion ids), and an agent error is an
`<error>`. Suite `<property>` entries carry the build, versions, hashes, pass rate, variances
and gate result.

### Release gate and exit codes

| Exit | Meaning |
|---|---|
| 0 | Every scenario's pass rate is at least `--min-pass-rate` (default `1.0`), and, when `--max-variance` is set, its accuracy variance is at most that value |
| 1 | The gate failed (`gate.failures` names each scenario and reason) |
| 2 | Usage or configuration error (bad suite, refused publish, unknown callable, ...), printed as JSON on stderr |

For deterministic agents over fixed fixtures, keep the default `--min-pass-rate 1`. For LLM
agents, run `--trials N` and set the floor and the variance cap that your release policy
accepts.

## `sim bench compare`

```bash
uv run sim bench compare var/bench/main.json var/bench/$GIT_SHA.json --tolerance 0.05 \
  --json var/bench/compare.json
```

Scenarios are matched by key. Each matched scenario gets:

* `deltas`: pass rate and mean accuracy, from the evaluator's `compare_builds`, plus mean
  action count and duration.
* Per-assertion pass-rate deltas.
* `status`:
  * `regressed`: the pass rate, the mean accuracy or any assertion's pass rate dropped by
    more than `--tolerance`.
  * `improved`: one of them rose and nothing dropped.
  * `unchanged`: otherwise.

If the two sides used a different scenario version, fixture hash or answer-key version, the
scenario is still compared, but it is listed in `summary.not_comparable` with the
differences. Scenarios that only one side has are listed under `added` or `removed`.

Exit codes: 0 no regression; 1 regression (or, with `--fail-on-missing`, a baseline scenario
is missing from the candidate); 2 when an input is not a `bench run` result.

For a REST alternative that groups stored evaluations by build for one scenario, see
`GET /v1/scenarios/{key}/accuracy`.

## Example CI job

```bash
uv run sim db-upgrade && uv run sim project-create   # fresh CI database
uv run python -m mailsim.serve serve --port 8000 &    # if the agent uses an HTTP interface
uv run sim bench run scenarios/ --build "$GIT_SHA" --agent-cmd "./run-agent.sh" \
  --api-url http://127.0.0.1:8000 --trials 3 --min-pass-rate 0.9 \
  --json bench.json --junit bench.xml                 # exit 1 fails the job
uv run sim bench compare baseline/bench.json bench.json   # baseline: e.g. main's artifact
```
