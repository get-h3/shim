# Changelog

## [0.2.0] — 2026-10-08

### Fixed
- **Pre-update-check no longer blocks every runnable Hermes version** (DF-H3-36):
  the 0.21.x row is recognized and planned rows only WARN instead of failing
  the check.
- Loader circuit-breaker recovery + durable session reroute (DF-H3-33/34/35):
  an OPEN circuit can return to CLOSED via a half-open probe fed by the health
  loop; reroutes survive process restarts.
- CLI `verify` prints the protocol value instead of the enum repr (DF-H3-37);
  the fallback report derives its defaults from the loader (DF-H3-38); the
  phantom v0→v1 schema-migration warning is gone (DF-H3-39).
- Battery: DELETE /v1/sessions terminate-shape coverage (H3-PM-019); battery
  prints target health identity, warns on stale co-tenant servers, adds
  `--expect-fresh` (DF-H3-26); names the "do not finish" convention in the
  test_2_4 failure detail (DF-H3-29).
- `--categories` accepts display-label aliases (DF5-H3-SHIM-4).
- Plugin install: nested/stale install warning; docs fix the install
  directory (DF5-H3-SHIM-3).
- Sessions purge on END decision in all three scaffolds + battery leak
  assertion (DF5-H3-SHIM-2); default Context embeds config+session_state so
  bare-Context embeds pass strict wire validation (DF5-H3-SHIM-1).
- Scaffold /v1/health capabilities match the decisions the example emits
  (DF4-H3-SHIM-4); integer duration_ms (DF4-H3-SHIM-1); session-GC END-purge
  in go + ts scaffolds (DF4-H3-SHIM-2).
- CI: version-gated PyPI publish + protocol sync fixes (PROTO-CI-002);
  lint failures fixed (unused import, import order, E402) and the
  uptime-dependent circuit-breaker test anchored to the real monotonic clock.

### Changed
- Test battery grown to **48 compliance tests** (was 46).
- Package made publish-ready: PEP 639 license metadata, full
  `[project.urls]` (DOGFOOD-01); first PyPI release of `hermes-h3-shim`.


## [0.1.0] — 2026-07-19

### Added
- Core H3 protocol implementation: Pydantic models, REST client, shim loop
- CLI with 8 subcommands: `hermes h3 {health,process,result,cancel,install,scaffold,verify,test}`
- Test battery: 44 compliance tests across 6 regions (E2E region-style)
- Go, Python, and TypeScript scaffold templates
- Native Hermes loop adapter
- Pre-flight upgrade check hook
- Sync protocol for regenerating types from OpenAPI spec

### Infrastructure
- GitReins quality gate with LLM evaluator (deepseek-v4-flash)
- Hilo code graph (116 edges, 18 files)
- 178 unit tests, ruff linting
