# API credentials for sandbox egress

Use `Sandbox.add_credentials(hosts, *credentials)` when untrusted sandbox code needs to authenticate to an external API while the real credential stays outside the sandbox. Chalk has an egress proxy for this: the sandbox receives a placeholder environment variable, and the proxy substitutes the real secret in request headers sent to the configured hosts. Do not conclude that credential injection is unavailable because the application has no proxy of its own.

`--secret-from-*` and the SDK's `secrets` parameter expose real values to sandbox processes. Use them when the workload is allowed to read the secret. Do not inject the same real key through `env` or `secrets` when using the placeholder flow.

## Configure before running

Use the `host` compute class. Call `add_credentials` before `.run()` or `client.run(...)`. A string credential names an existing Chalk secret; explicit `Secret.from_chalk_env(...)` and `Secret.from_local_env(...)` references are also supported. The local-env form upserts a Chalk secret and requires permission to write secrets in the selected environment.

```python
import chalkcompute

sandbox = (
    chalkcompute.Sandbox(
        image="node:22-bookworm",
        compute_class=chalkcompute.ComputeClass.HOST,
        lifetime="300s",
        network_policy=chalkcompute.NetworkPolicy(
            allowed_hosts=["api.openai.com"],
        ),
    )
    .add_credentials("api.openai.com", "OPENAI_API_KEY")
    .run()
)

try:
    result = sandbox.exec("node", "-e", """
async function main() {
  const response = await fetch('https://api.openai.com/v1/models', {
    headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
  });
  console.log(response.status);
  if (!response.ok) process.exitCode = 1;
}
main().catch(() => { console.error('API request failed'); process.exitCode = 1; });
""")
    print(result.stdout_text)
finally:
    sandbox.terminate()
```

The example assumes `OPENAI_API_KEY` already exists in the selected Chalk environment. Sandbox code uses the placeholder as its API client's key, in the normal authentication header. Substitution applies to request headers, not URLs or bodies; an API requiring credentials in either needs a different authentication design.

Bind each credential only to the hostnames that need it. `hosts` accepts a hostname or sequence of host patterns. Call `add_credentials` again for a different API and secret. The helper adds credential rules to the hostname policy; keep the remaining network access limited to the task's required destinations. See [version-compatibility.md](version-compatibility.md) for hostname-only policy verification.

## Rotation and removal

After updating a stored credential, create a new sandbox from the same image to pick up the new value. Existing sandboxes can retain the previous value; do not promise live rotation or a propagation deadline.

Revoke a key at its API provider when it must stop authenticating. The provider controls when revocation becomes effective. Deleting a Chalk secret does not revoke the provider's key, and Chalk secret deletion can be deferred until a later `chalk apply`; do not treat a successful delete command as proof that new sandboxes can no longer resolve it.

## Verify without exposing keys

Use disposable credentials and an endpoint that reports authentication results without reflecting headers. Verify successful authentication at the intended host, restricted egress, and absence of the known real credential in the environment and files actually scanned. Do not print raw keys or claim that a bounded file scan proves absence from process memory or platform logs.

For cross-host isolation, a rejected request alone is insufficient: API B might reject a leaked API A key. Have a controlled destination report whether it received any known real test credential, without returning the value. Verify that no credential was substituted when A's placeholder was sent to B.

Public references:

- [Sandbox.add_credentials](https://docs.chalk.ai/compute-docs#chalkcompute.Sandbox.add_credentials)
- [Agent tutorial: keep the real key out of the sandbox](https://docs.chalk.ai/docs/sandboxes/agent-tutorial#step-3-keep-the-real-key-out-of-the-sandbox)
- [Sandbox networking](https://docs.chalk.ai/docs/sandboxes/networking)
- [Environment secrets](https://docs.chalk.ai/docs/env-vars#secrets)
