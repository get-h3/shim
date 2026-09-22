# DF5-H3-SHIM-3 — plugin install nesting trap, staleness warning, exit codes

**Task:** `DF5-H3-SHIM-3` (P1, board `.coding-hermes/board/tasks.jsonl:100`)
**Date:** 2026-09-22 · **Box:** kara (worktree `/home/kara/worktrees/shim-DF5-H3-SHIM-3`,
branch `wt/DF5-H3-SHIM-3`)

Three defects in the optional `h3/` Hermes plugin, all three fixed here:

| # | Defect | Evidence |
|---|--------|----------|
| a | The documented install command copies the fresh copy *into* an existing plugin dir, nesting it and leaving the stale copy serving | §1 |
| b | `register()` had no way to notice the running mirror was older than the CLI it mirrors | §2 |
| c | A failing `hermes h3 <cmd>` could exit **0** — the failure code travelled as a *return value*, which a host may discard | §3 |

The board row's live evidence (found 2026-09-22, not re-derived here): the
registered plugin was the stale 2026-08-07 mirror whose `_OPTIONS` lacked the
`verify` positional and `route --session`, while the repo copy declared both;
the argparse mirror was never the bug — the trap was stale-copy + exit-code.

---

## 1. (a) Install docs target the parent directory

`docs/integration.md` §3.4 now documents the parent-target form and gains a
"Refreshing an existing install" note that names the nesting trap, the
`rsync --delete` refresh, the `diff` / `_plugin_version.txt` sanity check, and
the exit-code contract. The same correction was applied to the plugin
docstring (`h3/__init__.py`), the troubleshooting table
(`docs/integration.md`, two rows), and the in-repo usage skill
(`skills/h3-shim-usage/SKILL.md`, install line + lesson 14).

```
$ grep -n "cp -r h3" docs/integration.md
213:cp -r h3 ~/.hermes/plugins/
432:| `hermes h3 --help` → `error: argument command: invalid choice: 'h3'` | Plugin not installed or not enabled — see §3.4 (`cp -r h3 ~/.hermes/plugins/` (parent) + `hermes plugins enable h3`). |

$ grep -rn "cp -r h3 ~/.hermes/plugins/h3/" docs/integration.md h3/   # the old form
$ echo $?
1
```

Only current-state documentation surfaces were changed. Dated field reports
(`docs/dogfood/2026-08-07-integration.md`, `docs/dogfood/2026-09-22-integration.md`),
board/state stores (`.coding-hermes/*`), and the board row itself keep the old
command verbatim — they are era-correct records of what the docs said then.

The trap, reproduced live on a throwaway `HERMES_HOME` (never the real
`~/.hermes`):

```
== the nesting trap, live: cp -r h3 INTO the existing plugin dir ==
$HOME/plugins/h3/h3/__init__.py      <-- fresh copy, inert (nested)
$HOME/plugins/h3/__init__.py         <-- stale copy, still the one that loads
```

## 2. (b) Staleness warning in `register()`

`h3/__init__.py` ships `PLUGIN_VERSION = "0.3.0"` (mirrored by the repo file
`h3/_plugin_version.txt`). After a clean install the two agree; `register()`
compares the **installed** copy's marker against the running copy's
`PLUGIN_VERSION` and warns on either signature:

* a nested `h3/` directory inside the installed plugin dir (the `cp -r` nest), or
* a missing / unparseable / older `_plugin_version.txt` marker.

The warning goes to **both** the Hermes logger and stderr (a silent trap needs
a surface that a script operator actually sees), names the offending path, and
names the fix command. Staleness is a warning, not a refusal: the command group
still registers.

```
== clean install (parent-target form) ==
$HOME/plugins/h3/__init__.py
marker: 0.3.0
warning on a healthy install:
(none above = clean install)

== 3. register() warning on stderr (nested install) ==
WARNING: h3 plugin install looks STALE (nested install:
'$HOME/plugins/h3/h3' exists — a previous install copied INTO this directory
instead of replacing it, so the stale copy keeps serving). The CLI mirror you
are running may not match the hermes-h3 CLI it delegates to. Fix: reinstall
with 'cp -r h3 ~/.hermes/plugins/' (parent target, no pre-existing h3 dir) or
refresh in place with 'rsync -a --delete h3/ ~/.hermes/plugins/h3/'.
exit status of the same invocation (still loads — warning, not refusal):
RC=0

== 4. older marker (installed mirror predates this copy) ==
WARNING: h3 plugin install looks STALE (installed mirror is stale: marker says
'0.1.0', this copy is 0.3.0). ...

== 5. why a refresh triggers this: the real box install is pre-marker ==
$ ls ~/.hermes/plugins/h3/
__init__.py  __pycache__  plugin.yaml        <-- no _plugin_version.txt
```

Reproduce with `/tmp/h3shim_df5_3_staleness_demo.sh` (full output saved as
`/tmp/h3shim_df5_3_staleness_out.txt`).

## 3. (c) A failing `hermes h3` now exits non-zero

### Root cause

The handler delegated to the click CLI with `standalone_mode=False`, which
makes click **return** the exit code (the command's int return value, or
`ctx.exit(n)`'s code) instead of raising. The handler discarded that return
value and signalled failures only through its own return value. Hermes Core
turns a handler's non-zero int return into the process exit code
(`hermes_cli/main.py:3427-3431`), but that is a host convention, not a
contract — a host that calls the handler and discards the value
(`hermes_cli/main.py:14779-14781` in the `hermes-agent-tj` checkout on this
box) turns **every** delegated-CLI failure into exit 0. That is the reported
"`hermes h3` exits 0 on the failure" symptom: no script or CI gate can see it.

### Fix

`_handler` now fails the process the way both conventions already handle —
`SystemExit`:

* the in-process branch captures click's returned code and propagates it;
* `SystemExit` payloads are normalised (`None` → 0, `int` → itself, a *string*
  payload → printed to stderr, exit 1 — it previously became exit 0 with the
  message dropped);
* the subprocess branch propagates the child's status, mapping a signal death
  to the shell convention (128 + N);
* `hermes-h3` missing from PATH exits 1 with the existing message.

### Probe: the same handler, both host dispatch conventions

`/tmp/h3shim_df5_3_exit_probe.py` imports the plugin **from its repo path**
(exactly as Hermes does) and drives `_handler` through the plugin's own
`_setup()` tree; host convention A honours an int return, convention B
discards it.

Before the fix (`/tmp/h3shim_df5_3_probe_prefix.txt`):

```
[PASS] failing `route --session <unknown>` exits non-zero on host A (honours int return): exit status 1
[FAIL] failing `route --session <unknown>` exits non-zero on host B (discards int return): exit status 0
[PASS] valid `list` stays exit 0 on host A: exit status 0
[PASS] valid `list` stays exit 0 on host B: exit status 0
[PASS] argparse error ['no-such-subcommand'] exits 2: SystemExit(2) — not swallowed by the handler
[PASS] argparse error ['verify', '--no-such-flag'] exits 2: SystemExit(2) — not swallowed by the handler
[FAIL] string-coded SystemExit is normalised to a non-zero int: returned None
[FAIL] non-zero click main() return value is propagated: returned None

PROBE FAIL: 3 check(s) failed
```

After the fix (`/tmp/h3shim_df5_3_probe_postfix.txt`), probe exit status 0:

```
[PASS] failing `route --session <unknown>` exits non-zero on host A (honours int return): exit status 1
[PASS] failing `route --session <unknown>` exits non-zero on host B (discards int return): exit status 1
[PASS] valid `list` stays exit 0 on host A: exit status 0
[PASS] valid `list` stays exit 0 on host B: exit status 0
[PASS] argparse error ['no-such-subcommand'] exits 2: SystemExit(2) — not swallowed by the handler
[PASS] argparse error ['verify', '--no-such-flag'] exits 2: SystemExit(2) — not swallowed by the handler
[PASS] string-coded SystemExit is normalised to a non-zero int: raised 1
[PASS] non-zero click main() return value is propagated: raised 2

PROBE PASS: every failing invocation reaches a non-zero process exit status
```

### Live certificate: real `hermes h3` invocations

`/tmp/h3shim_df5_3_live_battery.sh` installs the fixed plugin into a throwaway
`HERMES_HOME` and records the real exit status of `hermes h3 ...`, for **both**
plugin dispatch branches:

* `subprocess` — this box's default: `h3_shim` is not installed in the Hermes
  venv, so the plugin shells out to `hermes-h3`;
* `in-process` — `h3_shim` importable in the Hermes interpreter, driven by
  `/tmp/h3shim_df5_3_inproc_hermes.py` (it imports `h3_shim.cli` *before*
  handing off to `hermes_cli.main`, so the plugin's `_CLICK_GROUP` branch is
  the one that runs).

```
installed: /tmp/h3shim_df5_3_final/home/plugins/h3 (marker 0.3.0)
subprocess  valid: --help                              RC=0
subprocess  valid: list                                RC=0
subprocess  bad: unknown subcommand                    RC=2
subprocess  bad: unknown flag (verify)                 RC=2
subprocess  bad: unset required --endpoint             RC=2
subprocess  bad: bad --lang choice                     RC=2
subprocess  bad: unknown harness                       RC=1
subprocess  bad: unknown session                       RC=1
subprocess  bad: unreachable endpoint                  RC=1
subprocess  bad: battery, not an H3 target             RC=2

in-process  valid: --help                              RC=0
in-process  valid: list                                RC=0
in-process  bad: unknown subcommand                    RC=2
in-process  bad: unknown flag (verify)                 RC=2
in-process  bad: unset required --endpoint             RC=2
in-process  bad: bad --lang choice                     RC=2
in-process  bad: unknown harness                       RC=1
in-process  bad: unknown session                       RC=1
in-process  bad: unreachable endpoint                  RC=1
in-process  bad: battery, not an H3 target             RC=2
```

Full transcript: `/tmp/h3shim_df5_3_live_battery_out.txt`.

### Healthy paths are unchanged (criterion 5)

```
$ hermes h3 --help            RC=0      (both branches)
$ hermes h3 list              RC=0      (both branches)
$ .venv/bin/hermes-h3 --help  RC=0      (worktree venv, direct CLI)
$ .venv/bin/hermes-h3 list    RC=0      "no harnesses configured"
$ .venv/bin/hermes-h3 route   RC=0      "no sessions configured — ..."
$ .venv/bin/hermes-h3 verify --endpoint http://127.0.0.1:1   RC=1   (real failure)
```

## 4. Tests

`tests/test_plugin_exit_codes.py` (13 tests) drives the plugin's real
`_setup`/`_handler` pair: the real click group for the failure paths that need
no network, and a stub failure *carrier* for the cases whose real carrier (a
broken harness, a signal-killed child) cannot be produced hermetically. The
stale-warning test asserts the stderr surface too.

Red-proof — the same test file against the pre-fix plugin
(`/tmp/h3shim_df5_3_redproof/`, plugin from `git show HEAD:h3/__init__.py`):

```
8 failed, 5 passed in 0.13s
FAILED test_failing_invocation_raises_so_an_ignoring_host_still_fails
FAILED test_failing_invocation_exit_code_matches_the_click_cli
FAILED test_non_zero_click_main_return_is_propagated
FAILED test_string_system_exit_is_normalised_to_non_zero
FAILED test_subprocess_failure_propagates_its_exit_code
FAILED test_subprocess_signal_death_reports_shell_convention
FAILED test_missing_hermes_h3_fails_loudly
FAILED test_stale_install_warning_reaches_stderr
```

Full suite, worktree venv (`.venv/bin/python -m pytest -q`), 2026-09-22:

| run | result |
|-----|--------|
| before this change (`git stash`-free baseline at HEAD) | `469 passed, 6 skipped` |
| after this change | **`482 passed, 6 skipped`** |

Delta = +13, exactly `tests/test_plugin_exit_codes.py`. No test was skipped for
this change (the 6 skips are pre-existing, host-dependent rows).

## 5. Residual (out of this change's scope)

* `AGENTS.md:10` still reads "copy to `~/.hermes/plugins/h3/`" — a *destination*
  phrasing, not a command, but it is the same ambiguity. The edit was refused
  by the agent-instruction-file approval gate (no user present), so it is left
  for a human/approved pass.
* The installed copy at `~/.hermes/plugins/h3/` predates this commit and has no
  `_plugin_version.txt`; until it is refreshed it is exactly the "pre-marker"
  case the new warning reports. Refresh (from this repo):
  `rsync -a --delete h3/ ~/.hermes/plugins/h3/`.
* `docs/dogfood/*` and `.coding-hermes/*` intentionally keep the old command as
  era-correct records.
