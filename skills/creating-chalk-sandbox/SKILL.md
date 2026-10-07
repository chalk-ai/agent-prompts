---
name: creating-chalk-sandbox
description: Use when creating, configuring, running commands in, or cleaning up a Chalk sandbox with `chalk sandbox create` — image, CPU/memory/GPU, env vars and secrets, local files and volumes, egress routes (allowedRoutes), lifetime, and one-shot versus long-lived sandboxes.
---

# Creating a Chalk Sandbox

## When to Use

- Provisioning a sandbox with `chalk sandbox create`
- Running a one-off command or an interactive shell in a fresh sandbox
- Passing env vars, secrets, local files, or volumes into a sandbox
- Restricting a sandbox's outbound network access
- Making sure a sandbox does not stay running by accident

## How a Sandbox Behaves

A sandbox outlives whatever you run in it. Unless you pass `--entrypoint`, the server keeps it alive on its own, and `--exec` runs as a separate process. After that process exits, the sandbox keeps running until you `chalk sandbox terminate` it or its `--lifetime` expires.

`--lifetime` defaults to `0`, which means forever. Choose one of these every time:

- One-shot run: add `--rm`, which terminates the sandbox once the `--exec` command finishes.
- Sandbox you keep: pass `--lifetime` (for example `8h`) so it cleans itself up.

Use `chalk sandbox list` to find sandboxes left running.

## Common Recipes

```bash
# Interactive shell, terminated on exit
chalk sandbox create -i ubuntu:22.04 --rm -t --exec bash

# One-shot script with local files uploaded into the sandbox
chalk sandbox create -i python:3.13 --rm \
  --with-local-file ./hello.py:/app/hello.py --exec python -- /app/hello.py

# Resources and env
chalk sandbox create -i python:3.12 --cpu 2 --memory 4Gi --env-file .env --rm --exec python -- /app/train.py

# Named, long-lived sandbox you exec into later
chalk sandbox create -i python:3.12 -n my-sandbox --lifetime 8h
chalk sandbox exec --name my-sandbox -- python -c "print('hello')"
chalk sandbox terminate --name my-sandbox
```

## Key Flags

| Flag | Purpose |
|------|---------|
| `-i, --image` | Container image |
| `-n, --name` | Optional name, usable later with `--name` on `exec`, `get`, `terminate` |
| `--cpu`, `--memory`, `--gpu` | Limits, such as `2`, `4Gi`, `nvidia-tesla-t4:1` |
| `--env`, `--env-file` | Environment variables (`key=value` pairs, or a `KEY=VALUE` file) |
| `--secret-from-chalk` | Inject a secret already in Chalk as env vars |
| `--secret-from-integration` | Inject an integration's secrets, as `NAME` or `NAME:KEY1,KEY2` |
| `--secret-from-local-env`, `--secret-from-local-env-file` | Create a Chalk secret from a local env var or env file and inject it |
| `--with-local-file` | Upload a local file to an auto-created volume and mount it, as `LOCAL:CONTAINER` |
| `--volume` | Mount an existing volume, such as `chalk://my-data@v2` |
| `--route` | Allowed outbound route, `CIDR[=PORT[,PORT-PORT,...]]`, repeatable |
| `--compute-class` | `host` (default) or `k8s` |
| `--lifetime`, `--rm` | Cleanup, see above |
| `--exec`, `--exec-tty`, `-t` | Run a command after creation |
| `--tags` | `key=value` tags |

Prefer `--secret-from-*` over `--env` for credentials, so values are not left in shell history or the spec.

## Network Routes (allowedRoutes)

`--route` takes CIDRs, not hostnames.

- With `--compute-class host` (the default for sandboxes), egress is denied unless routes are configured. When you pass no `--route`, the CLI supplies an allow-all policy (`0.0.0.0/0`).
- Passing any `--route` replaces that default. Only the listed CIDRs and ports are reachable.
- With `--compute-class k8s`, a missing policy means unrestricted egress.

### Resource detection

A sandbox spec can enable resource detection (`resource_detection` on the container spec). A detector inspects egress traffic to the hosts it recognizes, and the only detector today is GitHub pull requests. Detection has no effect for hosts the network policy does not allow.

When resource detection is enabled, keep `allowedRoutes` from conflicting with the hosts of the detectors in use:

- Do not add routes that block or shadow the detector's hosts. A narrow `--route` list that omits them silently disables detection.
- Do not add a catch-all or overlapping route that changes how traffic to those hosts is handled, unless the user asked for it.
- Resolve the detector's hosts before choosing routes, and say which routes you left out or kept because of them.

The CLI does not expose a resource-detection flag today, so this applies to sandboxes created from a spec through the API or SDK. The exact hosts for the GitHub detector are not documented here. Confirm them with the user or the hypervisor code before writing routes.

## Checklist Before Creating

1. Image and name decided.
2. Cleanup chosen: `--rm` for one-shot, `--lifetime` for anything kept.
3. Credentials passed through secrets, not `--env`.
4. Routes: either none (allow-all on host) or an explicit list that still covers everything the workload needs, including any resource-detection hosts.
5. After creating, confirm with `chalk sandbox list` and clean up with `chalk sandbox terminate`.
