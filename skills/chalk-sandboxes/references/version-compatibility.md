# CLI and SDK Version Compatibility

Use the network-policy syntax supported by the installed CLI and SDK while preserving the same effective sandbox policy. Verify the result end to end in the target Chalk environment before creating additional sandboxes.

## Check for updates regularly

While actively using Chalk sandboxes, check the CLI, SDK, and installed skill package for compatible updates at least weekly. Always check before an important or repeatable multi-sandbox run. Do not update tools without the user's authorization; if updates are approved, apply them before the run and keep the exact versions fixed until that run finishes.

Record all three inputs with the run results:

- CLI version or build identifier;
- `chalkcompute` package version;
- installed skill package version, when available.

Use `chalk version` and `chalk sandbox create --help` for the CLI. For the SDK, use package metadata rather than assuming the imported module exposes `__version__`:

```bash
python -c 'from importlib.metadata import version; print(version("chalkcompute"))'
```

Before an important or multi-sandbox run, use current supported CLI and SDK releases when the user authorizes the update, re-read the create help/signature, and run one canary. If updating is not possible, pin and report the installed versions and adapt only to behavior their help/API actually supports.

## Version-aware route configuration

The invariant is the effective policy, not a particular flag:

- ordinary egress uses exact hostname allowances;
- `allowed_routes` and `denied_routes` are empty unless the user explicitly requests IP/CIDR firewall controls;
- this applies equally to one-off and long-running sandboxes.

Select the hostname-only form documented by the installed CLI:

- If `chalk sandbox create --help` provides `--no-routes`, combine it with the required `--host` entries and never combine it with `--route`.
- If the installed CLI documents that omitted CIDR routes produce an empty route list, omit both `--route` and `--no-routes` and provide the required `--host` entries.

In either form, inspect the saved sandbox policy and confirm that the allow-route and deny-route lists are empty. If the installed CLI cannot express this policy, obtain authorization to update it or use a supported SDK. Never add a catch-all route as a compatibility workaround.

With the Python SDK, construct a hostname policy directly and leave both route collections unset:

```python
from chalkcompute import NetworkPolicy

network_policy = NetworkPolicy(
    allowed_hosts=["github.com", "api.github.com"],
)
```

Do not pass `allowed_routes` or `denied_routes`, including for durable sandboxes.

## Canary after an update

Create one short-lived host sandbox and inspect its saved spec before scaling. Verify:

1. the image, compute class, identity, detector, and hostname rules survived serialization;
2. the effective allowed-route and denied-route lists are empty;
3. an allowed hostname succeeds and an unlisted destination is blocked;
4. for artifact mode, a GraphQL `createPullRequest` operation produces a durable artifact.

Only then launch the remaining sandboxes. If CLI help, saved configuration, and runtime behavior disagree, stop at the canary and contact Chalk Support before scaling the workload.
