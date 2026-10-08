# Troubleshooting

Diagnose from the outside in: control-plane context, sandbox status, placement, process state, then application behavior.

## First capture

```bash
chalk whoami
chalk sandbox get --name "$SANDBOX_NAME" --json
chalk sandbox list --all --search "$SANDBOX_NAME"
```

Record the environment, sandbox ID, status message, creation and finish times, requested resources, host pool, and exit code before retrying or terminating anything.

## Authentication and environment

### `dial tcp [::1]:4002: connect: connection refused`

The CLI is targeting a local API endpoint. Use the intended API host and the matching auth-config, scope, and environment on both login and subsequent commands. Check exported `CHALK_*` variables and shell aliases before retrying.

### `Client ID and secret are invalid`

Confirm the credentials belong to the same control plane as the API host. Re-enter them without extra quotes or whitespace. Do not store raw credentials in the repository.

### Team or environment not found / permission denied

This usually means a control-plane or scope mismatch, or that the active identity lacks the named permission. `whoami` can list access without proving the command is targeting the intended environment. Make the environment explicit and request only the missing permission, such as `secrets.write` or `deploy.read`.

## Scheduling

### `no configured host pool can satisfy`

No single host is large enough. Reduce the request or enlarge a pool, leaving memory headroom for host overhead. More hosts do not make one larger logical host.

### `all registered host-pool capacity is allocated`

The pool fits the request but is full. Wait, terminate unused sandboxes, or raise maximum hosts.

### A sandbox used the wrong pool

Check whether a cluster-scoped pool exists; cluster pools override environment-scoped pools. Use the exact `--host-pool` ID when supported and confirm placement after the canary starts.

## Process and attachment

### Exit code `137`

The process received `SIGKILL`; OOM is common when memory reached the limit, but maintenance or another forced termination can look similar. Correlate memory peak, status message, platform events, and audit logs.

### `container ... not found` while attaching

Fetch sandbox status first. If it is failed, terminated, succeeded, or expired, report that terminal state, finish time, and exit code; tmux cannot be recovered from a nonexistent container. If maintenance or an external delete occurred, record the responsible operation from audit logs when available.

### Sandbox is running, but no tmux server exists

The container can be healthy without tmux. `chalk sandbox attach --tmux` only discovers existing tmux sessions and never launches an agent. Start a named tmux session with `chalk sandbox exec`, then attach again.

### Tmux exists but the agent does not

Inspect inside the session with `tmux list-panes -a -F '#{session_name} #{pane_pid} #{pane_current_command}'` and `ps`. A tmux server or pane can survive after its child agent exits. Do not describe that as a restored agent session.

### Snapshot/resume returns zombie or missing processes

Treat this as an infrastructure restoration defect, not a tmux usage issue. Capture the source sandbox, snapshot/resumed sandbox IDs, `tmux ls`, `ps` output, and relevant hypervisor logs. Until an end-to-end test passes, rely on durable files plus idempotent restart logic rather than process checkpointing.

## Observability and artifacts

### Logs were visible live but disappear after termination

Sandbox stdout/stderr may not be durably retained after the container is gone. Write important logs to a mounted volume or external log sink before termination. Missing historical logs are not proof that nothing ran.

### No access logs or traces

Prove that the sandbox actually made an eligible outbound request after it became running. A live but idle sandbox does not generate access activity.

### GitHub PR is missing from Artifacts

Follow [artifacts.md](artifacts.md). The most common causes are a missing detector, GitHub traffic taking an overlapping CIDR route, missing hostname allowances, or creating only a branch rather than a PR.
