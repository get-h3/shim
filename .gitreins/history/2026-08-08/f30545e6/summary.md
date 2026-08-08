# Verdict: gap-009

**Task:** hermes h3 accepts --config after the subcommand
**Evaluated:** 2026-08-08T02:54:46.754753
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ hermes h3 list --config <path> parses and runs (exit 0, no unrecognized-arguments error) — live-verified via hermes CLI with a minimal config: Live CLI: `hermes h3 list --config /tmp/h3test/minimal.yaml` → exit 0, output 'no harnesses configured', no unrecognized-arguments error
  ✓ hermes h3 --config <path> list (config before subcommand) still parses and runs (exit 0): Live CLI: `hermes h3 --config /tmp/h3test/minimal.yaml list` → exit 0, output 'no harnesses configured'
  ✓ Repo h3/__init__.py registers --config (dest h3_config, default=argparse.SUPPRESS) on EVERY subparser via _add_config_option, and the deployed copy at ~/.hermes/plugins/h3/__init__.py is byte-identical (cmp): h3/__init__.py:108-124 _add_config_option uses dest='h3_config', default=argparse.SUPPRESS; called on all 9 subparsers (lines 143,161,164,191,195,213,236,242,254). `cmp h3/__init__.py ~/.hermes/plugins/h3/__init__.py` → IDENTICAL
  ✓ docs/integration.md troubleshooting includes an entry stating --config is accepted before OR after the subcommand (older plugin builds were parent-only): docs/integration.md:274 troubleshooting table: 'Older plugin builds registered --config only on the parent parser. Current builds accept --config before OR after the subcommand in hermes h3...'
  ✓ tests/test_h3_plugin.py exists with tests covering both --config orders and passes in the full suite: tests/test_h3_plugin.py exists (163 lines); test_config_accepted_both_orders parametrized with config-after-subcommand and config-before-subcommand; full suite 286 passed (17 from test_h3_plugin.py)
All 5 criteria verified: both --config orders work live via hermes CLI (exit 0), _add_config_option registers --config (dest h3_config, default=SUPPRESS) on all 9 subparsers with byte-identical deployed copy, docs troubleshooting entry present, and tests covering both orders pass in the full suite.

## Summary

Judge Result: gap-009

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ hermes h3 list --config <path> parses and runs (exit 0, no unrecognized-arguments error) — live-verified via hermes CLI with a minimal config: Live CLI: `hermes h3 list --config /tmp/h3test/minimal.yaml` → exit 0, output 'no harnesses configured', no unrecognized-arguments error
  ✓ hermes h3 --config <path> list (config before subcommand) still parses and runs (exit 0): Live CLI: `hermes h3 --config /tmp/h3test/minimal.yaml list` → exit 0, output 'no harnesses configured'
  ✓ Repo h3/__init__.py registers --config (dest h3_config, default=argparse.SUPPRESS) on EVERY subparser via _add_config_option, and the deployed copy at ~/.hermes/plugins/h3/__init__.py is byte-identical (cmp): h3/__init__.py:108-124 _add_config_option uses dest='h3_config', default=argparse.SUPPRESS; called on all 9 subparsers (lines 143,161,164,191,195,213,236,242,254). `cmp h3/__init__.py ~/.hermes/plugins/h3/__init__.py` → IDENTICAL
  ✓ docs/integration.md troubleshooting includes an entry stating --config is accepted before OR after the subcommand (older plugin builds were parent-only): docs/integration.md:274 troubleshooting table: 'Older plugin builds registered --config only on the parent parser. Current builds accept --config before OR after the subcommand in hermes h3...'
  ✓ tests/test_h3_plugin.py exists with tests covering both --config orders and passes in the full suite: tests/test_h3_plugin.py exists (163 lines); test_config_accepted_both_orders parametrized with config-after-subcommand and config-before-subcommand; full suite 286 passed (17 from test_h3_plugin.py)
All 5 criteria verified: both --config orders work live via hermes CLI (exit 0), _add_config_option registers --config (dest h3_config, default=SUPPRESS) on all 9 subparsers with byte-identical deployed copy, docs troubleshooting entry present, and tests covering both orders pass in the full suite.

Overall: PASS ✓
