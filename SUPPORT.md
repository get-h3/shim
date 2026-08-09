# Support — H3 Shim

H3 Shim is a Hermes plugin maintained by the get-h3 project.

## Getting Help

- **Bug reports:** https://github.com/get-h3/shim/issues
- **Protocol questions:** https://github.com/get-h3/h3/discussions
- **Security issues:** See SECURITY.md for responsible disclosure

## Supported Harnesses

The shim works with any harness implementing the H3 protocol. Pre-built SDKs:

- Go: https://github.com/get-h3/sdk-go
- Python: https://github.com/get-h3/sdk-python
- TypeScript: https://github.com/get-h3/sdk-typescript

## Running the Test Battery

```
pip install hermes-h3-shim
h3-test --endpoint http://localhost:9191
```

44 compliance tests. Exit 0 = compliant.
