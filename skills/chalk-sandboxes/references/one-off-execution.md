# One-Off Execution

Use this mode for a single command, smoke test, conversion, or short interactive investigation. Optimize for a small request, explicit inputs, captured output, and automatic cleanup.

## Default pattern

```bash
chalk sandbox create \
  --image node:22-bookworm \
  --cpu 1 \
  --memory 2Gi \
  --lifetime 30m \
  --no-routes \
  --rm \
  --exec node -- -e 'console.log("hello")'
```

This example uses the hostname-only route syntax supported by CLI versions that provide `--no-routes`. If the installed CLI documents hostname-only egress without this flag, use that syntax instead. See [version-compatibility.md](version-compatibility.md).

`--rm` terminates the sandbox when the exec command finishes, including when the command exits non-zero. The lifetime is a second safety bound if the client disappears before cleanup. Preserve the remote command's exit status and stdout/stderr in the result.

For an interactive shell that is cleaned up on exit:

```bash
chalk sandbox create --image node:22-bookworm --lifetime 30m --no-routes --rm -t --exec bash
```

For a local input file:

```bash
chalk sandbox create \
  --image node:22-bookworm \
  --with-local-file ./job.js:/app/job.js \
  --lifetime 30m \
  --no-routes \
  --rm \
  --exec node -- /app/job.js
```

## Decisions to make

- Use the smallest realistic CPU and memory request. Increase it from evidence, not by default.
- Add a host pool only when placement matters; do not add durable-workspace setup to a disposable run.
- Use `--secret-from-*` for credentials.
- Restrict ordinary egress with `--host` entries, and keep the effective route lists empty even for long-running work. Do not set allowed or denied routes unless the user is explicitly asking for IP/CIDR firewall control. Follow the version-compatible hostname-only syntax in [version-compatibility.md](version-compatibility.md).
- Use a Chalk Volume only when the output must survive cleanup. A local upload is an input mechanism, not durable output storage.

After any create/exec error, check `chalk sandbox list --all --search NAME` to ensure cleanup happened. Explicitly terminate a leaked sandbox.
