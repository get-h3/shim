# H3 Shim — Hermes Integration Guide

How to install the H3 shim, register a harness, wire Hermes routing, and
verify everything end to end.  A new user following this document from
scratch can go from zero to a verified harness.

The shim ships two console scripts:

| Script | Purpose |
|--------|---------|
| `h3-test` | One-shot H3 compliance test battery against an endpoint (`h3-test --endpoint <url> [--json] [--categories ...]`) |
| `hermes-h3` | Harness management CLI — 9 subcommands: `install`, `list`, `pre-update-check`, `route`, `scaffold`, `test`, `uninstall`, `use`, `verify` |

Both are defined in `src/h3_shim/cli.py` and registered in `pyproject.toml`
(`[project.scripts]`).  The optional `hermes h3` plugin (this repo's `h3/`
directory) exposes the same 9 subcommands as a Hermes Core command group —
see [Optional: the `hermes h3` plugin](#optional-the-hermes-h3-plugin).

---

## 1. Install the shim

```bash
pip install git+https://github.com/get-h3/shim
```

This installs the `hermes-h3-shim` package and the two console scripts
above.  The package is **not** published to PyPI yet, which is why the
install is from the git URL.

Everything is driven by one YAML config file, created on demand:

```text
~/.hermes/h3/config.yaml
```

The CLI never requires the file to pre-exist — `load_config()` in
`src/h3_shim/cli.py` returns an empty skeleton when it is absent, and
`hermes-h3 install`, `hermes-h3 use`, and `hermes-h3 scaffold` create it.
Every `hermes-h3` command accepts `--config <path>` to point at a
different file.

## 2. Register a harness

```bash
# Register a harness and make it the default
hermes-h3 install my-harness --endpoint http://localhost:9191 --set-default

# Inspect what is registered
hermes-h3 list

# Switch the default later
hermes-h3 use my-harness

# Remove a harness
hermes-h3 uninstall my-harness
```

Real flags for `install` (from `src/h3_shim/cli.py`):

```text
hermes-h3 install NAME --endpoint URL [--transport rest]
                       [--timeout-ms 30000] [--set-default]
```

- `--transport` defaults to `rest`.
- `--timeout-ms` defaults to `30000`.
- `--set-default` promotes the harness to `default_harness`.  When no
  default exists yet, the first installed harness becomes the default
  automatically.
- `hermes-h3 scaffold` with no `--lang` writes an empty config skeleton;
  with `--lang go|py|ts` it generates a complete harness project in a new
  `h3-harness-<lang>/` subdirectory (rendered from
  `src/h3_shim/templates/<lang>/`).
- `hermes-h3 route` prints the current session → harness routing table.

## 3. Configure Hermes routing

### 3.1 Where the shim actually lives (correction to AGENTS.md)

`AGENTS.md` previously claimed the shim lives at
`hermes_cli/agent/shims/h3/` inside Hermes Core.  **That path does not
exist** — it was verified against the Hermes Core checkout
(`~/.hermes/hermes-agent/hermes_cli/agent/` has no `shims/` directory),
and this repo is a standalone Python package, not a directory inside
Hermes Core.

The real integration surface is:

1. The `hermes-h3-shim` pip package (this repo), which provides the CLI,
   the client, the loader, the shim loop, and the test battery.
2. `~/.hermes/h3/config.yaml` — the single source of truth for which
   harnesses exist and how sessions route to them.
3. The optional `h3/` plugin in this repo, which registers the
   `hermes h3` command group with Hermes Core (see 3.4).

There is no code to drop into Hermes Core and no Hermes Core
modification is required.

### 3.2 Config semantics (`src/h3_shim/loader.py` + `cli.py`)

```yaml
default_harness: my-harness        # fallback route; "native" if unset
harnesses:
  my-harness:
    endpoint: http://localhost:9191
    transport: rest                # default "rest"
    timeout_ms: 30000              # default 30000
sessions:
  "telegram:-1001234567890": my-harness          # plain string = harness name
  "telegram:-1001234567890:42":
    harness: my-harness           # or {harness: name}
identity:                         # optional — sent as auth on every request
  hermes_token: <token>
  hermes_identity: <identity>
  protocol_version: "1.0"
max_consecutive_failures: 3       # default 3 — failures before reroute
circuit_breaker_window: 20        # default 20 — sliding window size
circuit_breaker_threshold: 0.5    # default 0.5 — error rate that opens
circuit_breaker_cooldown: 30.0    # default 30s — cooldown before half-open
```

Key behaviours implemented in `H3Loader` (`src/h3_shim/loader.py`):

- **Discovery** — every entry in `config["harnesses"]` becomes an
  `H3Client` (except the reserved name `"native"`, which maps to Hermes'
  own agent loop and has no HTTP endpoint).  Entries without an
  `endpoint` are skipped.
- **Health checking** — a background task health-checks every harness
  every 30 seconds.  After `max_consecutive_failures` (default 3)
  consecutive failures, sessions routed to the failed harness are
  rerouted to `default_harness`.
- **Circuit breaker** — a sliding-window breaker (window 20, threshold
  50%) opens on sustained failures and reroutes sessions *immediately*;
  while OPEN, health checks are skipped; after the cooldown (30s) a
  half-open probe decides whether to close or re-open.
- **Session routing** — `resolve(platform, chat_id, thread_id)` matches
  the `sessions` map most-specific-first:
  `platform:chat_id:thread_id` → `platform:chat_id` → `platform` →
  `default_harness`.  Sessions can also be pinned explicitly in code via
  `route_session(session_id, harness_name)`.

### 3.3 The session loop (`src/h3_shim/shim_loop.py`)

`H3ShimLoop` drives one H3 session:

1. POST the user message to the harness (`/v1/process`).
2. Inspect the returned `Decision`.
3. Execute the decision locally (tool call, LLM call, text, wait,
   delegate, …).
4. POST the `ExecutionResult` back (`/v1/result`).
5. Repeat until the harness returns an `END` decision.

It enforces a hard iteration cap (`max_iterations`, default 50) so a
misbehaving harness cannot spin a session forever, and propagates
`asyncio.CancelledError` by asking the harness to cancel its session.

### 3.4 Optional: the `hermes h3` plugin

This repo ships a Hermes Core plugin in the top-level `h3/` directory
(`plugin.yaml` + `__init__.py`).  It registers an `h3` subcommand group
with Hermes Core via `register_cli_command`, mirroring the `hermes-h3`
CLI — `hermes h3 --help` lists the same 9 subcommands, and each
invocation is delegated to the real CLI (in-process when the shim is
installed in the same interpreter as Hermes, otherwise by shelling out
to the `hermes-h3` executable).

```bash
# Install the plugin (copy the directory from this repo)
cp -r h3 ~/.hermes/plugins/h3/

# Enable it (user plugins are opt-in)
hermes plugins enable h3

# Use it
hermes h3 --help
hermes h3 list
hermes h3 install my-harness --endpoint http://localhost:9191 --set-default
```

The plugin is a thin delegate: it does not duplicate CLI logic, so the
two entry points can never drift.  `hermes-h3` itself always works
without the plugin.

## 4. Verify

### 4.1 Get a harness running

Either scaffold a fresh one:

```bash
hermes-h3 scaffold --lang go     # or py / ts
cd h3-harness-go
go mod tidy && go run .          # listens on http://localhost:9191
```

…or run one of the SDK echo examples (battery-passing reference
implementations):

| SDK | Echo example | Run |
|-----|--------------|-----|
| Go | `sdk-go/examples/echo` | `go run .` |
| Python | `sdk-python/src/h3_harness/examples/echo.py` | `python echo.py` |
| TypeScript | `sdk-typescript/src/examples/echo.ts` | `npm run build && npm start` |

Default listen ports (none of the echo examples read the ``PORT`` env var):

| SDK | Echo example | Default port |
|-----|--------------|--------------|
| Go | `sdk-go/examples/echo` | **9191** |
| Python | `sdk-python/src/h3_harness/examples/echo.py` | **8000** |
| TypeScript | `sdk-typescript/src/examples/echo.ts` | **9191** |

### 4.2 Health-check it

```bash
hermes-h3 verify                       # uses the default harness from config
hermes-h3 verify --harness my-harness  # a specific harness
hermes-h3 verify --endpoint http://localhost:9191   # skip config entirely
hermes-h3 verify --fallback            # also report the native-fallback path
```

Expected output (healthy harness): `status: ok`, plus `version`,
`capabilities` when the harness reports them.  Exit code 0.

### 4.3 Run the compliance battery

```bash
hermes-h3 test                          # battery against the default harness
h3-test --endpoint http://localhost:9191            # same battery, one-shot
h3-test --endpoint http://localhost:9191 --json     # machine-readable report
h3-test --endpoint http://localhost:9191 --categories health,process
```

The battery is 44 tests across 6 categories (health, process, decisions,
results, errors, stress).  See [Exit codes](#exit-codes) below for the
meaning of each h3-test exit code.

### Exit codes

`h3-test` (and `hermes h3 test` / `hermes-h3 test`) uses three distinct
exit codes so CI can tell a genuine compliance failure apart from a
connection/typing mistake:

| Code | Meaning | What it means in CI |
|------|---------|---------------------|
| `0`  | **Compliant** — the target is an H3 endpoint and every check passed. | Green: ship it. |
| `1`  | **Compliance failure** — the target is a real H3 endpoint (it answered `/v1/health` with an H3-shaped payload), but one or more protocol checks failed. | Red: the harness has a protocol bug. Inspect the per-test detail (or `--json`) and fix the harness. |
| `2`  | **Not an H3 endpoint** — the target did not look like an H3 harness: connection refused, non-JSON body, HTTP >= 400 (incl. 401 unauthorized), JSON with `status != "ok"`, or missing `version`/`protocol_version`/`transport`/`capabilities` fields. | Amber: this is NOT a compliance failure. The URL is wrong, the harness is down, or you pointed h3-test at the wrong server. A stderr warning (`... does not look like an H3 endpoint (reason)`) is printed; in `--json` mode the report carries `"not_h3_endpoint": true`. |

Do not treat exit 2 as a protocol regression — a dead or wrong-port
server will exit 2, not 1.

### 4.4 Verify routing

```bash
hermes-h3 route                          # shows the session → harness table
```

Add a route in `~/.hermes/h3/config.yaml` under `sessions` and confirm it
appears in the table.  The loader applies most-specific-first matching
(§3.2), and the native loop is always the fallback when a harness is
unreachable.

## Troubleshooting

| Symptom | Cause / fix |
|---------|------------|
| `hermes h3 --help` → `error: argument command: invalid choice: 'h3'` | Plugin not installed or not enabled — see §3.4 (`cp -r h3 ~/.hermes/plugins/h3/` + `hermes plugins enable h3`). |
| `Error: no harness specified and no default_harness set` | No harness registered — `hermes-h3 install <name> --endpoint <url> --set-default`. |
| `Error: harness 'x' not found in config` | Name mismatch — `hermes-h3 list` shows the registered names. |
| `verify failed for 'x': ...` | Harness not running or wrong endpoint — check it is up on the port you registered. |
| Battery exits non-zero | Check the exit code: **0** = compliant, **1** = real compliance failure (run with `--json` and inspect per-test failures; the SDK echo examples are the compliance reference), **2** = not an H3 endpoint (wrong URL / harness down / connection refused / HTTP error) — NOT a protocol regression. See [Exit codes](#exit-codes). |
| `hermes h3 list --config X` works but `hermes h3 --config X list` (or vice-versa) errored | Older plugin builds registered `--config` only on the parent parser. Current builds accept `--config` **before OR after** the subcommand in `hermes h3` (matching the standalone `hermes-h3` click CLI) — re-copy `h3/` from this repo to `~/.hermes/plugins/h3/`. |
