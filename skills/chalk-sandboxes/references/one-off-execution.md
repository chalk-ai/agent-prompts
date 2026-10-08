# One-Off Execution

Use this mode for a single command, smoke test, conversion, or short interactive investigation. Optimize for a small request, explicit inputs, captured output, and automatic cleanup.

## Default pattern

```bash
chalk sandbox create \
  --image python:3.13 \
  --cpu 1 \
  --memory 2Gi \
  --lifetime 30m \
  --rm \
  --exec python -- -c 'print("hello")'
```

`--rm` terminates the sandbox when the exec command finishes, including when the command exits non-zero. The lifetime is a second safety bound if the client disappears before cleanup. Preserve the remote command's exit status and stdout/stderr in the result.

For an interactive shell that is cleaned up on exit:

```bash
chalk sandbox create --image ubuntu:22.04 --lifetime 30m --rm -t --exec bash
```

For a local input file:

```bash
chalk sandbox create \
  --image python:3.13 \
  --with-local-file ./job.py:/app/job.py \
  --lifetime 30m \
  --rm \
  --exec python -- /app/job.py
```

## Decisions to make

- Use the smallest realistic CPU and memory request. Increase it from evidence, not by default.
- Add a host pool only when placement matters; do not add durable-workspace setup to a disposable run.
- Use `--secret-from-*` for credentials.
- Restrict egress with exact `--host` entries or `--route` CIDRs when needed. If no explicit host or route policy is supplied to a host-class sandbox, the CLI may add permissive egress; do not mistake that convenience for a production policy.
- Use a Chalk Volume only when the output must survive cleanup. A local upload is an input mechanism, not durable output storage.

After any create/exec error, check `chalk sandbox list --all --search NAME` to ensure cleanup happened. Explicitly terminate a leaked sandbox.
