# Changelog

## [0.1.0] — 2026-07-19

### Added
- Core H3 protocol implementation: Pydantic models, REST client, shim loop
- CLI with 8 subcommands: `hermes h3 {health,process,result,cancel,install,scaffold,verify,test}`
- Test battery: 43 compliance tests across 6 regions (E2E region-style)
- Go, Python, and TypeScript scaffold templates
- Native Hermes loop adapter
- Pre-flight upgrade check hook
- Sync protocol for regenerating types from OpenAPI spec

### Infrastructure
- GitReins quality gate with LLM evaluator (deepseek-v4-flash)
- Hilo code graph (116 edges, 18 files)
- 178 unit tests, ruff linting
