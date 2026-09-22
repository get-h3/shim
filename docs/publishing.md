# Publishing `hermes-h3-shim` to PyPI

How the shim gets from this repository to `pip install hermes-h3-shim`.
Everything here is run by the **release owner** — the person holding the PyPI
API token. Nothing in this document (or the repo) contains a token value.

The package is `v0.1.0`-ready: `pyproject.toml`, the built artifacts and the
metadata they carry are audited below. The index itself is the only thing that
cannot be verified from inside the repo, so step 4 is the proof.

---

## 0. Prerequisites

- A PyPI account with an **API token** scoped to this project (PyPI → Account
  settings → API tokens → *scope: project `hermes-h3-shim`*).
- The token exported in the environment of the release shell as
  `PYPI_API_TOKEN`. Never commit it, never paste it into a command line that
  gets logged, never put it in this repo — `.gitignore` excludes `.env*` and
  the upload reads it from the environment only:

  ```bash
  # in the release shell only, e.g. from a secret manager or ~/.hermes/env-file
  export PYPI_API_TOKEN=...   # value never written to disk in this repo
  ```

## 1. What PyPI will see

`pyproject.toml` is the single source of truth for the package identity:

| Field | Value / why it matters |
|---|---|
| `name` | `hermes-h3-shim` — reserved; the index name is permanent |
| `version` | `0.1.0` — **immutable on PyPI once uploaded** (see §5) |
| `requires-python` | `>=3.10` |
| `license` / `license-files` | PEP 639: SPDX expression `MIT` plus the three files that carry it (`LICENSE`, `NOTICE`, `NOTICE.md`). No `License ::` classifier — a classifier next to a `License-Expression` is the one combination PEP 639 lets a build tool reject |
| `[project.urls]` | Homepage / Repository / Issues / Changelog / Documentation |
| `dependencies`, `[project.optional-dependencies].dev` | runtime vs dev split |
| `[project.scripts]` | `h3-test`, `hermes-h3` |

The sdist is assembled from the working tree, so `[tool.hatch.build.targets.sdist]`
excludes the internal working state that lives beside the source — the foreman
board (`.coding-hermes/**`), GitReins state, the Hilo graph cache, the DAGger
run store and `*.bak` files. **A published sdist must never carry those.**

## 2. Build the artifacts

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pip install build twine          # or: make build-dist (build only)
.venv/bin/python -m build                  # -> dist/*.whl + dist/*.tar.gz
.venv/bin/python -m twine check dist/*     # both lines must say PASSED
```

`dist/` is gitignored — build artifacts are never committed.

Sanity-check the metadata before uploading:

```bash
tar xzOf dist/hermes_h3_shim-0.1.0.tar.gz hermes_h3_shim-0.1.0/PKG-INFO | head -30
# expect: License-Expression: MIT, five Project-URL lines, Author-email,
#         and NO "License:" field and NO "License ::" classifier
tar tzf dist/hermes_h3_shim-0.1.0.tar.gz | grep -E 'coding-hermes|gitreins|\.vfs|dagger\.db'
# expect: no output
```

## 3. Upload

```bash
TWINE_USERNAME=__token__ \
TWINE_PASSWORD="$PYPI_API_TOKEN" \
  .venv/bin/python -m twine upload \
    dist/hermes_h3_shim-0.1.0-py3-none-any.whl \
    dist/hermes_h3_shim-0.1.0.tar.gz
```

- The username is the literal string `__token__`; the password is the token,
  read from the environment variable. Only the variable *name* appears here.
- Both files are named explicitly so a stale leftover in `dist/` cannot be
  uploaded by accident. (`dist/*` is equivalent once the directory is clean.)
- Optional rehearsal first, against TestPyPI — needs a separate token scoped to
  TestPyPI: `twine upload --repository testpypi ...` then
  `pip install --index-url https://test.pypi.org/simple/ hermes-h3-shim`.

## 4. Verify the upload

```bash
# a) the index now serves the project (404 before the first upload)
curl -sS -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/hermes-h3-shim/json
# expect: 200

# b) a scratch venv installs it and the battery actually loads
python3 -m venv /tmp/h3-verify
/tmp/h3-verify/bin/pip install --upgrade pip hermes-h3-shim
/tmp/h3-verify/bin/h3-test --endpoint http://127.0.0.1:1 ; echo "exit=$?"
# expect: "does not look like an H3 endpoint" and exit=2 — the battery ran and
#         reported "not an H3 endpoint" (nothing is listening on port 1).
#         A missing/broken install would exit 127 or traceback instead.
```

Then the real end-to-end proof against a live harness (exit `0` = compliant):

```bash
/tmp/h3-verify/bin/hermes-h3 scaffold --lang py
cd h3-harness-py && python3 -m venv .venv && source .venv/bin/activate
pip install -e . && python main.py                 # listens on :9191
/tmp/h3-verify/bin/h3-test --endpoint http://localhost:9191
```

The three exit codes (`0` compliant, `1` compliance failure, `2` not an H3
endpoint) are documented in the README.

## 5. Bump a version, then publish

Versions live in more than one file — bump **all** of them in one commit, or
the wheel and the runtime disagree:

| File | What it holds |
|---|---|
| `pyproject.toml` → `version` | the version PyPI receives |
| `src/h3_shim/__init__.py` → `__version__` | what `h3-test --version` and the banner print |
| `src/h3_shim/data/versions.yaml` | the Hermes ↔ H3 compatibility row; its `h3_shim` entry must stay **>=** the shipped version or `pre-update-check` can never pass |
| `tests/test_cli.py` → `test_version_accessible` | pins the literal version string |
| `CHANGELOG.md` | add a dated section for the new version |

```bash
# 1. edit the files above (e.g. 0.1.1)
# 2. prove the tree is green
make test && make lint
# 3. rebuild and re-check
.venv/bin/python -m build && .venv/bin/python -m twine check dist/*
# 4. upload (same command as §3, new filenames), then re-verify §4
# 5. tag the release on main
git tag v0.1.1 && git push origin v0.1.1
```

PyPI refuses to overwrite an existing version: a docs-only fix still needs a
version bump. Never reuse a version number that has ever been uploaded.

## 6. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `404` from the JSON API | release not uploaded yet (or wrong project name) | upload via §3 |
| `403 Invalid or non-existent authentication information` | token not exported, wrong scope, or trailing whitespace | re-export `PYPI_API_TOKEN` and retry |
| `400 File already exists` | that version was uploaded before | bump the version, rebuild, retry |
| `twine check` FAILED | README rendering or metadata problem | fix `README.md` / `pyproject.toml`, rebuild |
| `pip install` finds nothing | upload succeeded to TestPyPI only | upload to PyPI proper |
