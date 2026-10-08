# GitHub Pull Request Artifacts

Resource detection records resources created through sandbox egress. Today the supported detector is `github-pull-requests`. Creating a Git branch or commit is not enough; create a GitHub pull request from the sandbox.

## Current safe configuration

The deployed network-policy precedence currently requires a temporary constraint: use hostname allowances for GitHub and do not add an overlapping or catch-all `--route`. A CIDR route can win before host-aware inspection, bypassing the HTTP/TLS hostname path the detector needs.

```bash
chalk sandbox create \
  --name "$SANDBOX_NAME" \
  --image node:22-bookworm \
  --cpu 2 \
  --memory 4Gi \
  --lifetime 8h \
  --chalk-identity \
  --secret-from-local-env GH_TOKEN \
  --resource-detection github-pull-requests \
  --host github.com \
  --host api.github.com
```

Hostname rules are exact. Add other GitHub download hosts only when the workflow actually needs them. Tell the agent to use GitHub hostnames, not resolved IP addresses. Do not add `--route '0.0.0.0/0'` as a convenience to this sandbox while the precedence workaround is required.

This constraint can be removed after the deployed hypervisor evaluates detector and allowed-host matches before overlapping allowed routes. Re-test artifact creation end to end before deleting the warning.

## Prove detection

Inside the sandbox:

1. Confirm GitHub authentication without printing the token.
2. Clone or open the intended repository.
3. Create a branch and commit a harmless change.
4. Push the branch.
5. Create the PR with `gh pr create` or the GitHub API using `api.github.com`.

Then inspect the sandbox's **Artifacts** tab or the sandbox-resources API. Record the sandbox ID and PR URL so missing detection can be reproduced.

If the PR exists but the artifact list is empty, verify all of the following before escalating:

- `--resource-detection github-pull-requests` was present in the create request.
- `api.github.com` was in the hostname policy.
- No `--route` overlapped the GitHub destination.
- The PR was created after the sandbox was running, from traffic originating in that sandbox.
- The deployed API preserved the detector field and the running hypervisor includes the detector implementation.

If the installed CLI lacks `--resource-detection` or `--host`, update it rather than silently falling back to an undetectable configuration.
