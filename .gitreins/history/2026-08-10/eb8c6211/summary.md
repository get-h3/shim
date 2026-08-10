# Verdict: gap-014

**Task:** GAP-014: hermes-h3 accepts --config after the subcommand
**Evaluated:** 2026-08-10T12:55:38.493534
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ --config after subcommand exits 0 and uses the config: tests/test_cli.py::TestList::test_list_accepts_config_after_subcommand passes: runner.invoke(hermes_h3, ['list','--config',custom]) returns exit_code 0 and output contains 'zeta'/'http://z:9' from the custom config. Full test_cli.py suite: 80 passed.
  ✓ subcommand --config overrides group-level --config: tests/test_cli.py::TestList::test_config_after_subcommand_beats_group_option passes: group config (empty harnesses) + subcommand --config (omega) yields output with 'omega'/'http://o:7'. Code: each command does `if config_path is not None: ctx.obj['config_path'] = config_path` (cli.py lines 512, 542, 597, 620, 668, 854, 896, 927), overriding group-level value set in hermes_h3 callback.
  ✓ all 9 hermes-h3 subcommands accept --config: All 9 subcommands (test, list, install, uninstall, verify, scaffold, route, pre-update-check, use) show '--config FILE' in `hermes-h3 <cmd> --help`. 8 use the _config_option decorator (cli.py lines 489,537,565,614,633,849,874,921); scaffold uses an inline --config option (cli.py line 774).
  ✓ : 
GAP-014 fully implemented: all 9 hermes-h3 subcommands accept --config after the subcommand, subcommand value overrides group-level option, and both new tests pass (80/80 test_cli.py).

## Summary

Judge Result: gap-014

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ --config after subcommand exits 0 and uses the config: tests/test_cli.py::TestList::test_list_accepts_config_after_subcommand passes: runner.invoke(hermes_h3, ['list','--config',custom]) returns exit_code 0 and output contains 'zeta'/'http://z:9' from the custom config. Full test_cli.py suite: 80 passed.
  ✓ subcommand --config overrides group-level --config: tests/test_cli.py::TestList::test_config_after_subcommand_beats_group_option passes: group config (empty harnesses) + subcommand --config (omega) yields output with 'omega'/'http://o:7'. Code: each command does `if config_path is not None: ctx.obj['config_path'] = config_path` (cli.py lines 512, 542, 597, 620, 668, 854, 896, 927), overriding group-level value set in hermes_h3 callback.
  ✓ all 9 hermes-h3 subcommands accept --config: All 9 subcommands (test, list, install, uninstall, verify, scaffold, route, pre-update-check, use) show '--config FILE' in `hermes-h3 <cmd> --help`. 8 use the _config_option decorator (cli.py lines 489,537,565,614,633,849,874,921); scaffold uses an inline --config option (cli.py line 774).
  ✓ : 
GAP-014 fully implemented: all 9 hermes-h3 subcommands accept --config after the subcommand, subcommand value overrides group-level option, and both new tests pass (80/80 test_cli.py).

Overall: PASS ✓
