# GitHub Pull Request Artifacts

Resource detection records resources created through sandbox egress. Today the supported detector is `github-pull-requests`. Creating a Git branch or commit is not enough; create a GitHub pull request from the sandbox.

## Current safe configuration

The deployed network-policy precedence currently requires a temporary constraint: use hostname allowances for GitHub and do not add an overlapping or catch-all `--route`. A CIDR route can win before host-aware inspection, bypassing the HTTP/TLS hostname path the detector needs.

For artifact-producing sandboxes, use Chalk workload identity and host-bound credential placeholders. A sandbox that merely receives a real GitHub token in `GH_TOKEN` can open a PR, but the request may not travel through the credential-substitution path the detector observes. Prefer the SDK to guarantee this shape. Treat these as required for PR artifacts unless a known-good CLI flag path provides the same egress-proxy credential binding:

- `chalk_identity=True`
- `compute_class=cc.ComputeClass.HOST`
- `resource_detectors=[cc.ResourceDetector.GITHUB_PULL_REQUESTS]`
- exact GitHub host allowances for the operation
- `Sandbox.add_credentials(...)` for GitHub API hosts, rather than `env={"GH_TOKEN": Secret.from_chalk_env(...)}`

The deployed detector currently recognizes GitHub GraphQL `createPullRequest` mutations on `api.github.com`. It does not record PRs created with the REST `POST /repos/{owner}/{repo}/pulls` endpoint, even though those PRs are real. Create the PR with `gh pr create` or a `createPullRequest` GraphQL mutation (its `repositoryId` input is the repository's `node_id`). REST is fine for preparing branches and commits.

### Preferred SDK shape

`Sandbox.add_credentials(...)` binds a Chalk secret to specific hosts. The sandbox sees only a placeholder value; the egress proxy swaps in the real secret only on requests to those hosts. Bind the token to the GitHub API hosts only.

```python
import chalkcompute as cc

sandbox = cc.Sandbox(
    image=cc.Image.sandbox().apt_install(["gh", "tmux"]),
    cpu="2",
    memory="4Gi",
    lifetime="1800s",
    chalk_identity=True,
    compute_class=cc.ComputeClass.HOST,
    network_policy=cc.NetworkPolicy(
        allowed_hosts={
            "github.com": [],
            "api.github.com": [],
            "uploads.github.com": [],
        },
    ),
    resource_detectors=[cc.ResourceDetector.GITHUB_PULL_REQUESTS],
    tags={"purpose": "agent-pr-artifacts"},
)
sandbox.add_credentials(
    ["api.github.com", "uploads.github.com"],
    cc.Secret.from_chalk_env("GH_TOKEN", alias="GH_TOKEN"),
)
sandbox.run()
```

If the workflow pushes over Git HTTPS, `github.com` traffic needs its own host-bound credential or hostname transform. Do not put a real token in the sandbox environment and build `Authorization` headers from it.

### CLI fallback

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

Use the CLI fallback only when its available flags can express the credential behavior you need. `--secret-from-chalk GH_TOKEN` or `--secret-from-local-env GH_TOKEN` may be sufficient for process authentication, but by itself it is not proof that GitHub API traffic used detector-visible credential substitution. If a CLI-created sandbox opens the PR but no artifact appears, retry with the SDK pattern above before blaming the detector. Hostname rules are exact. Add other GitHub download hosts only when the workflow actually needs them. Tell the agent to use GitHub hostnames, not resolved IP addresses. Do not add `--route '0.0.0.0/0'` as a convenience to this sandbox while the precedence workaround is required.

This constraint can be removed after the deployed hypervisor evaluates detector and allowed-host matches before overlapping allowed routes. Re-test artifact creation end to end before deleting the warning.

## Prove detection

Inside the sandbox:

1. Confirm GitHub authentication without printing the token.
2. Clone or open the intended repository.
3. Create a branch and commit a harmless change.
4. Push the branch.
5. Create the PR with `gh pr create` or a GraphQL `createPullRequest` mutation using `api.github.com` (not REST `POST /pulls`; see above).

Then inspect the sandbox's **Artifacts** tab or the sandbox-resources API. Record the sandbox ID and PR URL so missing detection can be reproduced.

If the PR exists but the artifact list is empty, verify all of the following before escalating:

- `--resource-detection github-pull-requests` was present in the create request.
- `api.github.com` was in the hostname policy.
- No `--route` overlapped the GitHub destination.
- The sandbox used workload identity (`--chalk-identity` or `chalk_identity=True`).
- GitHub API credentials were bound with `Sandbox.add_credentials(...)`, or the CLI flow was known to use equivalent host-bound placeholder substitution. A raw `GH_TOKEN` environment variable is not enough evidence.
- The PR was created by `gh pr create` or a GraphQL `createPullRequest` mutation, not REST `POST /pulls`.
- The PR was created after the sandbox was running, from traffic originating in that sandbox.
- The deployed API preserved the detector field and the running hypervisor includes the detector implementation.

If the installed CLI lacks `--resource-detection` or `--host`, update it rather than silently falling back to an undetectable configuration.
