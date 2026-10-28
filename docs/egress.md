# Network-level egress block

Spec section 10, "No external delivery": block outbound SMTP at the network level, do not rely
only on application checks. The application-level guarantees (no SMTP/socket/HTTP client in
`src/`, ruff banned-api + AST test, `DeliveryPolicy` deny-by-default) stay; this is the layer
underneath them for the Docker Compose deployment.

## Topology

```
              sandbox (internal: no default route)
  +-------------------------------------------------------------------+
  |  app (sim CLI / runner)   api (REST + IMAP/SMTP)   egress-check   |
  |            \                   |                    /             |
  |             +------ postgres --+---- egress-proxy -+              |
  +-----------------------|-----------------|-------------------------+
                     host-access          egress (routable)
              (published 127.0.0.1:54329)   |
                                    HTTPS CONNECT to EGRESS_ALLOWLIST only
```

* **Simulator containers** (`app`, `api`, `egress-check`; profile `app`) join **only**
  `sandbox`. Docker gives an `internal: true` network no gateway, so a connect to any external
  address fails ("network unreachable", or a DNS failure by name) on every port, whatever code
  runs inside, including an agent that tries to open its own SMTP connection.
* **`egress-proxy`** (`deploy/egress_proxy/`, standard library only) is the only container on
  both `sandbox` and the routable `egress` network. It tunnels HTTPS `CONNECT` to an exact
  `host:port` allowlist and refuses everything else: other hosts or ports, SMTP included (403),
  plain HTTP forwarding (405), malformed or oversized requests (400/431). Matching is exact and
  case-insensitive (one trailing dot ignored); there are no wildcards, so neither
  `x.api.anthropic.com` nor an IP literal passes unless listed. It runs read-only, as a non-root
  user, with all capabilities dropped.
* The simulator containers get `HTTPS_PROXY`/`HTTP_PROXY=http://egress-proxy:3128`,
  `NO_PROXY=postgres,localhost,127.0.0.1` and `MAILSIM_ENV=deployment`, so a non-stub LLM
  provider refuses to start without the proxy (`mailsim/llm/config.py`), and the Anthropic SDK
  and OpenAI-compatible client both route through it.
* **`postgres`** also joins `host-access` so the host can reach the published loopback port.
  It runs no simulator or agent code.
* The `api` service publishes no host ports (an internal network cannot). Run the agent under
  test in a container attached to `sandbox`; it then reaches `api:8000` (REST), `api:1143`
  (IMAP) and `api:1587` (SMTP submission, STARTTLS with a self-signed certificate) and, like
  the simulator, nothing outside.

## Configuration

| Variable (host, read by Compose) | Default | Meaning |
| --- | --- | --- |
| `MAILSIM_EGRESS_ALLOWLIST` | `api.anthropic.com:443` | comma-separated `host[:port]` the proxy may tunnel to (port defaults to 443) |
| `MAILSIM_EGRESS_CHECK_ALLOWED` | `api.anthropic.com:443` | endpoint the `egress-check` service expects the proxy to open |

Keep the allowlist to the LLM endpoint(s) you actually use. An OpenAI-compatible endpoint on
the host (e.g. Ollama) is not reachable from `sandbox`; run it as a container on `sandbox`
instead, or add its `host:port` to the allowlist (it must speak HTTPS through `CONNECT`).

## Proving it

```bash
export COMPOSE_PROFILES=app
docker compose build
docker compose up -d --wait postgres egress-proxy
docker compose run --rm app db-upgrade
docker compose up -d --wait api
docker compose run --rm egress-check     # simulator image, `sandbox` network, proxy env
```

`tests/egress/egress_check.py` (standard library only; mounted read-only, run with `python -I`)
asserts, from inside the container:

1. direct TCP connects to `smtp.gmail.com`, `smtp.office365.com`, `1.1.1.1`, `8.8.8.8` on
   25/465/587, and to `example.com`, `1.1.1.1`, `8.8.8.8` on 80/443, all fail (by name and by IP
   literal, so a DNS failure alone cannot make it pass);
2. the proxy answers 403 to CONNECT `smtp.gmail.com:25/465/587` and `example.com:443`, and 405
   to an absolute-URI `GET http://example.com/`;
3. CONNECT to each `--allowed` endpoint is established (200), so the LLM path works; the tunnel
   is closed at once and nothing is sent through it;
4. each `--reachable` in-sandbox service (postgres, the API and its IMAP/SMTP listeners)
   connects, so the block is not simply "no network at all".

The CI job `egress` (`.github/workflows/ci.yml`) runs the probe in the `egress-check` service,
in a one-off `app` container, and inside the running `api` container (`docker compose exec`).
Before that it runs the probe in `--expect-open` mode from the same image on the default bridge
network, where `1.1.1.1:443` and `example.com:443` must connect: that control proves the probe
can tell an open network from a blocked one. The control never attempts SMTP; mail ports are
only ever negative targets whose connects must fail.

`tests/egress/test_egress.py` (part of the normal pytest run, loopback sockets only) pins the
proxy's allowlist decisions, its tunnelling and error paths, and the probe's pass/fail logic.

## Other deployments

Outside Compose, reproduce the same shape: no default route or a deny-all egress policy for the
simulator (for example a Kubernetes `NetworkPolicy` allowing egress only to the proxy and the
database), outbound TCP 25/465/587 also dropped at the host or VPC firewall, and the allowlist
proxy as the single path to the LLM endpoint. Re-run `egress_check.py` inside the simulator
workload to verify.
