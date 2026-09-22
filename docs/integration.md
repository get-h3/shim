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
# PEP 668 distros (Ubuntu 24+, Debian 12+) refuse bare pip installs —
# always use a venv:
python3 -m venv .venv
source .venv/bin/activate
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

The config path is resolved in this order, highest first:

```text
--config <path>            (before or after the subcommand)
$HERMES_H3_CONFIG          (environment variable; ~ is expanded)
~/.hermes/h3/config.yaml   (default)
```

Exports are enough to redirect a whole session — every command that
reads or writes config honors it:

```bash
export HERMES_H3_CONFIG=/tmp/mine.yaml
hermes-h3 scaffold          # creates /tmp/mine.yaml, not the home path
hermes-h3 list              # reads /tmp/mine.yaml
```

A blank value (`HERMES_H3_CONFIG=""` or whitespace) falls back to the
default path, as does an unset variable. `pre-update-check` uses the
same order.

## 2. Register a harness

```bash
# Register a harness and make it the default
# (the harness must be up: install health-checks the endpoint first)
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
- `install` **health-checks the endpoint before writing anything**
  (DF-H3-SHIM-FOREMAN-3).  It issues `GET /v1/health` with the same client,
  transport and timeout the shim uses at runtime, and refuses to register an
  endpoint that is unreachable, does not answer like an H3 harness, or
  reports a status other than `ok`.  The failure exits non-zero, names the
  endpoint and the cause, and leaves the config file (including an existing
  `default_harness`) byte-for-byte untouched — a typo can no longer produce a
  config entry that only fails later at `verify`/first session.  If the
  harness is not running yet, start it before installing (or add the entry to
  `harnesses:` by hand and use `hermes-h3 verify` to re-check it).
- `hermes-h3 scaffold` with no `--lang` writes an empty config skeleton;
  with `--lang go|py|ts` it generates a complete harness project in a new
  `h3-harness-<lang>/` subdirectory (rendered from
  `src/h3_shim/templates/<lang>/`).
- `hermes-h3 route` prints the current session → harness routing table.  With
  no routes configured it exits 0 and prints where routes come from plus a
  minimal `sessions:` YAML example (see §4.4).  `--session <id>
  --set-harness <name>` writes a binding to the config file and `--remove`
  deletes one — no hand-edit needed (see §4.4).

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
# Install the plugin: copy the h3 directory into the plugins PARENT, so the
# result is ~/.hermes/plugins/h3/ and no pre-existing h3 dir is involved.
# (Copying the h3 directory INTO an existing plugin directory nests the
# fresh copy at .../h3/h3/ — see "Refreshing an existing install" below.)
cp -r h3 ~/.hermes/plugins/

# Enable it (user plugins are opt-in)
hermes plugins enable h3

# Use it
hermes h3 --help
hermes h3 list
hermes h3 install my-harness --endpoint http://localhost:9191 --set-default
```

**Refreshing an existing install.** An update REPLACES the installed copy in
place — use `rsync --delete` rather than copying the repo's `h3` directory
into the plugin directory:

```bash
rsync -a --delete h3/ ~/.hermes/plugins/h3/
```

Copying the repo's `h3` directory into an existing plugin directory
(`cp -r <repo>/h3 <plugins-dir>/h3/`) copies *into* it once it exists, leaving
a fresh copy nested at `~/.hermes/plugins/h3/h3/` while the stale copy at the
top level keeps serving. The failure is quiet, not loud: a subcommand form the
mirror's own help documents dies with `unrecognized arguments`, because the
running copy predates it. Before debugging the mirror, confirm the installed
copy is the one you think it is:

```bash
diff ~/.hermes/plugins/h3/__init__.py h3/__init__.py   # from a repo checkout
cat ~/.hermes/plugins/h3/_plugin_version.txt           # installed mirror version
```

`h3/__init__.py` ships `PLUGIN_VERSION` (currently `0.3.0`) and the repo's
`h3/_plugin_version.txt` carries the same string. An install keeps its own
marker file, so `register()` compares the *installed* mirror version against
the one the running copy expects and prints a `WARNING` (Hermes log **and**
stderr) when the install is nested, has no marker, or carries an older marker.
A stale install is still registered — the warning names the fix command rather
than refusing to load.

Failures are exit-code-honest: `hermes h3 <cmd>` raises the delegated CLI's
exit code, so a bad subcommand or bad args exits `2`, a failing command exits
`1`, and the battery's own codes (`hermes h3 test`, see [Exit codes](#exit-codes))
pass through unchanged. Scripts and CI gates can therefore trust the status.

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

The first `go mod tidy` downloads the scaffold's pinned dependency
(`github.com/get-h3/sdk-go`) from proxy.golang.org.  On a cold module
cache that fetch can take several minutes, and the only output while it
runs is a `go: downloading ...` line — it has not hung and has not
failed, so let it finish.  Later runs reuse the module cache and start
in seconds.

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

The battery is 46 tests across 6 categories (health, process, decisions,
results, errors, stress).  See [Exit codes](#exit-codes) below for the
meaning of each h3-test exit code.

### 4.4 The compliance gate (GAP-043) — wired into `make test` and CI

Since GAP-043 the 46-test battery is THE GATE: it runs against a live
scaffolded harness on every `make test` and every CI push, and a protocol
regression fails the build (h3-test exit 1) — no silent green.

```bash
make test          # pytest unit suite (317 tests) + the compliance gate
make test-battery  # the gate alone
bash scripts/test_battery.sh   # what the gate actually runs
```

The gate is fully self-contained — **no external checkout (get-h3/sdk-go)
is required**:

1. `hermes-h3 scaffold --lang py` generates a fresh harness from this
   repo's own template (`src/h3_shim/templates/py/`) into a `mktemp` dir.
2. The harness's own `requirements.txt` (fastapi/uvicorn/pydantic) is
   installed into a throwaway venv — the project's `pyproject.toml` deps
   are untouched.
3. The harness starts on a free port (auto-scanned from 9191) and is
   health-checked at `/v1/health`.
4. `h3-test --endpoint` runs the battery; the script asserts **exit 0
   AND `TOTAL 46/46 PASSED`**. Exit 1 (compliance) or 2 (unreachable /
   not-H3) both fail the build.

The endpoint defaults to the loader config
(`~/.hermes/h3/config.yaml` → `default_harness` → `endpoint`, currently
`http://localhost:9191`) and can be overridden with `H3_ENDPOINT`.

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
hermes-h3 route --session telegram:-100:42   # one row, fail-closed if absent
```

**Add or remove a binding from the CLI** (no hand-edit, works on an
existing config):

```bash
# bind a session to a harness (the harness must already be registered —
# see `hermes-h3 list` / `hermes-h3 install`)
hermes-h3 route --session "telegram:-1001234567890" --set-harness my-harness
# telegram:-1001234567890 -> my-harness (saved to /home/you/.hermes/h3/config.yaml)

hermes-h3 route                    # confirm the row appears in the table
hermes-h3 route --session "telegram:-1001234567890" --remove   # delete it
```

Both write flags need `--session <id>`; `--set-harness` also needs a harness
name that is present in the config's `harnesses:` map, and the two flags are
mutually exclusive.  Anything else exits non-zero, prints the reason (unknown
harness names are listed), and leaves the config file **untouched**.  A
repeated `--set-harness` for the same session/harness pair is idempotent.

**If the table is empty** (`no sessions configured — the routing table is
empty.`), the command exits 0 and prints the resolved config path, the YAML
to add, and why an empty table is normal:

```yaml
harnesses:
  my-harness:
    endpoint: http://localhost:9191
sessions:
  "telegram:-1001234567890": my-harness        # bare string = harness name
  "telegram:-1001234567890:42":
    harness: my-harness                        # or {harness: <name>}
```

**Or add it by hand** — put that under `sessions:` in the config file the
command names (default `~/.hermes/h3/config.yaml`; override with
`--config <path>` or `$HERMES_H3_CONFIG`) — then re-run `hermes-h3 route` and
confirm the row appears.  The loader applies most-specific-first matching
(§3.2), and the native loop is always the fallback when a harness is
unreachable.

**Why it can be empty even though routing "works".**  The table above is
read from the config file only.  Routes can *also* be pinned in memory at
runtime — `H3Loader.route_session(session_id, harness_name)` fills the
loader's run-scoped route map (read back with
`get_session_harness(session_id)`; the loader also rewrites it when a harness
fails), typically by an embedder or the shim loop.  Those runtime pins are
**never written back** to the config, so `hermes-h3 route` (a separate
process, reading the file) can legitimately show no sessions while a running
shim is routing them.  Adding a route to `sessions:` is what makes it visible
here and persistent across runs; the in-code pin is the programmatic
alternative.  Note the fallback order is independent: a session with no
`resolve()` match uses `default_harness`.

## Troubleshooting

| Symptom | Cause / fix |
|---------|------------|
| `hermes h3 --help` → `error: argument command: invalid choice: 'h3'` | Plugin not installed or not enabled — see §3.4 (`cp -r h3 ~/.hermes/plugins/` (parent) + `hermes plugins enable h3`). |
| `Error: no harness specified and no default_harness set` | No harness registered — `hermes-h3 install <name> --endpoint <url> --set-default`. |
| `Error: endpoint <url> failed its health check: ...` (exit 1) | `install` probed `GET /v1/health` and the endpoint is unreachable, is not an H3 harness, or reports a non-`ok` status — **nothing was written**. Fix the URL or start the harness, confirm with `hermes-h3 verify --endpoint <url>`, then re-run `install`. |
| `Error: harness 'x' not found in config` | Name mismatch — `hermes-h3 list` shows the registered names. |
| `verify failed for 'x': ...` | Harness not running or wrong endpoint — check it is up on the port you registered. |
| `hermes-h3 route` prints `no sessions configured` with a YAML example | No `sessions:` entries in the config and no runtime pin — add one with `hermes-h3 route --session <id> --set-harness <name>` (or under `sessions:` in the config path the message names, default `~/.hermes/h3/config.yaml`), or let a running shim/embedder pin it via `H3Loader.route_session(...)`. See §4.4. |
| `Error: --set-harness and --remove need --session <id>` / `Error: --set-harness and --remove are mutually exclusive` | The write flags need a target: pass `--session <id>` with exactly one of `--set-harness <name>` or `--remove`. **Nothing was written** (the config file is validated before any save). See §4.4. |
| Battery exits non-zero | Check the exit code: **0** = compliant, **1** = real compliance failure (run with `--json` and inspect per-test failures; the SDK echo examples are the compliance reference), **2** = not an H3 endpoint (wrong URL / harness down / connection refused / HTTP error) — NOT a protocol regression. See [Exit codes](#exit-codes). |
| `hermes h3 list --config X` works but `hermes h3 --config X list` (or vice-versa) errored | Older plugin builds registered `--config` only on the parent parser. Current builds accept `--config` **before OR after** the subcommand in `hermes h3` (matching the standalone `hermes-h3` click CLI) — refresh the install with `rsync -a --delete h3/ ~/.hermes/plugins/h3/` (never copy the repo's `h3` dir INTO `~/.hermes/plugins/h3/`, which nests the fresh copy and keeps serving the stale one). |
| A subcommand form that `hermes h3 --help` documents fails with `unrecognized arguments`, and `_plugin_version.txt` is missing or older than the repo's | The running copy is a stale and/or nested mirror. `register()` prints a `WARNING` (Hermes log + stderr) naming the offending path; refresh with `rsync -a --delete h3/ ~/.hermes/plugins/h3/` and delete any leftover `~/.hermes/plugins/h3/h3/`. See §3.4. |
