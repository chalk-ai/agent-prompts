# Function-based sandbox provisioning

Use this guidance when a deployed Chalk Function creates and returns a sandbox. Direct `chalk sandbox create` calls do not require a provisioning function.

## Prefer queued invocation

Default Python callers to `fn.defer(...)` for sandbox provisioning. Queued submission separates accepting the request from worker availability, so a cold start or worker replacement does not require the caller to establish a direct connection to a ready worker. `chalk sandbox start` provides the corresponding queued CLI flow.

Choose `.remote(...)` only when direct-call latency is an explicit workload requirement, with appropriate readiness checks and a bounded retry policy supported by the installed client. Do not switch a provisioner to direct invocation as a timeout workaround. Queued invocation does not require the provisioning function itself to be declared with `async def`.

## Default to at least two workers

Default sandbox provisioners to at least two function workers so another worker remains available during replacement:

```python
import chalkcompute

@chalkcompute.function(
    chalk_identity=True,
    # Keep another worker available during replacement.
    min_replicas=2,
    max_replicas=2,
)
def create_dev_sandbox(name_suffix: str, host_pool_id: str) -> chalkcompute.Sandbox:
    return chalkcompute.Sandbox(
        name="dev-" + name_suffix,
        image="node:22-bookworm",
        compute_class=chalkcompute.ComputeClass.HOST,
        host_pool_id=host_pool_id,
        lifetime="3600s",
    ).run()
```

This example retains the sandbox for one hour or until explicit termination; it also leaves the provisioner deployed. Add the identity, detector, credentials, hostname policy, volume mounts, and resources required by the actual task. For GitHub PRs, apply [artifacts.md](artifacts.md) to the returned sandbox.

These replica settings belong to the function workers, not the sandbox host pool. Confirm two workers from the new function version are Ready before testing; a deployment-level total can include a retiring worker. Size the host pool separately so each sandbox fits on one host. Keep an explicitly requested scale-to-zero or single-worker design when its availability tradeoff is acceptable; do not apply two replicas to every function.

Disruption budgets are platform-managed and depend on the deployed platform version. Verify a budget's selector and effective allowance when diagnosing disruptions; absence from a customer's GitOps repository alone does not prove that no live budget exists. Multiple workers and a disruption budget improve availability, but do not guarantee that an in-flight call cannot be interrupted or replayed. Do not change cluster consolidation or maintenance policy as a routine prerequisite.

## Submit once and retain the call ID

Prefer the queued CLI flow for interactive provisioning:

```bash
chalk sandbox start create_dev_sandbox \
  --arg name_suffix=session \
  --arg host_pool_id=HOST_POOL_ID \
  --no-attach
```

The CLI prints a provisioning call ID. Resume that same call after a timeout or disconnect:

```bash
chalk sandbox start --call-id CALL_ID --no-attach
```

In Python, attach to the existing deployment without redeploying and enqueue once:

```python
import chalkcompute

fn = chalkcompute.RemoteFunction.from_name("create_dev_sandbox")
handle = fn.defer(name_suffix="session", host_pool_id="HOST_POOL_ID")
print(handle.call_id, flush=True)
```

Supply all arguments required by the installed SDK and published function schema. Preserve the same environment and authentication when collecting the result. `chalk sandbox start` and `.defer()` both use queued invocation; switching between them is not a recovery mechanism. A client timeout stops waiting, not the remote call.

## Recover without duplicating resources

After a failed or uncertain call, inspect its status and the expected sandbox before submitting another call:

```bash
chalk sandbox get --name dev-session
chalk sandbox list
```

A failed call can leave a running sandbox. Verify its environment, identity and configuration before reconnecting; a matching name alone does not prove that it belongs to this request. Never terminate or adopt another caller's sandbox to clear a name collision.

For retry-safe provisioning, use a supported stable request identity and validate the requested configuration when recovering an existing resource. A fixed sandbox name alone is not idempotency. Preserve call IDs, sandbox IDs, timestamps and worker-version evidence when escalating interrupted execution; do not add an unbounded create/retry loop.

Run one canary through provisioning, readiness, required network access, detach/reconnect and explicit cleanup before a larger run. A normal-path pass does not establish recovery after a worker interruption.
