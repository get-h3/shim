# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

To report a security vulnerability, please open an issue on GitHub at [get-h3/shim](https://github.com/get-h3/shim) or contact the maintainers directly.

Do NOT disclose security vulnerabilities publicly until they have been addressed.

## Dependencies

This project uses `uv` for dependency management. Run `uv lock --upgrade` periodically to update dependencies. Security advisories for Python packages are monitored via GitHub's Dependabot and PyPI advisory database.

## Build Process

The shim does not execute arbitrary code from harnesses at build time. Harness communication is restricted to the H3 protocol over HTTP.
