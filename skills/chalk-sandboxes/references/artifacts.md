# GitHub Pull Request Artifacts

Resource detection records resources created through sandbox egress. Today the supported detector is `github-pull-requests`. Use this flow by default whenever a Chalk sandbox creates a GitHub pull request; the user does not need to request an artifact explicitly. Creating a Git branch or commit is not enough; create a GitHub pull request from the sandbox.

## Hard requirements

All of these are required by currently supported Chalk environments:

- Use host compute. GitHub pull-request detection is not supported for Kubernetes sandboxes.
- Enable `github-pull-requests` resource detection.
- Enable Chalk workload identity so detected resources can be associated with the sandbox.
- Allow `api.github.com` through the hostname policy.
- Create the pull request with GitHub's GraphQL `createPullRequest` mutation. `gh pr create` does this and is supported. GitHub's REST `POST /repos/{owner}/{repo}/pulls` is **not detected**.
- Use the configured hostname path. Do not use a resolved IP or an overlapping CIDR route for `api.github.com`.

These are compatibility requirements, not merely a preferred recipe. Do not replace the GraphQL mutation with a generic “GitHub API” call.

## Artifact-mode gate

Before creating an artifact-producing sandbox, assemble and check the effective configuration as one unit:

- compute class is `HOST`;
- Chalk workload identity is enabled;
- `GITHUB_PULL_REQUESTS` detection is enabled;
- `api.github.com` is in the hostname policy;
- the CIDR allow-route list is empty.

After creation, inspect the saved sandbox spec before making any GitHub mutation. If any assertion is false, terminate and recreate the sandbox with the safe configuration. Do not “try it anyway.” When broader network access is required, add the necessary hostnames individually; never add `0.0.0.0/0`, `::/0`, or another route that can carry `api.github.com` traffic in artifact mode.

## Resolve incompatible requests before creating anything

Treat detection as part of the normal sandbox PR workflow, not a best-effort add-on. Resolve these combinations before sandbox creation:

| Requested combination | Compatible path |
| --- | --- |
| Sandbox PR + Kubernetes compute | Use host compute for the PR-producing sandbox. If the workload must remain on Kubernetes, use a separate host publisher sandbox or explain that current PR detection is unsupported. |
| Sandbox PR + unrestricted/catch-all egress | Replace the catch-all route with the exact hostname allowlist. If unrestricted egress is non-negotiable, use a separate restricted publisher sandbox or explain that detection cannot be guaranteed. |
| Sandbox PR + REST-only GitHub workflow | REST may create the branch and content, but the final PR creation must be GraphQL `createPullRequest`. If REST-only is non-negotiable, explain that the PR will not be detected. |

When the user asks generally for a PR and names one of the incompatible settings only as an implementation preference, choose the compatible path without requiring the user to ask for detection. When the incompatible setting is an explicit hard requirement, do not silently proceed with a known-broken combination or claim detection; surface the conflict and request a choice or propose a separate host publisher sandbox.

## Current safe configuration

Use hostname allowances for GitHub and do not add an overlapping or catch-all `--route`. An overlapping CIDR route can prevent pull-request detection.

Use the hostname-only form documented by the installed CLI. The example below uses `--no-routes`; CLI versions that document an empty route list when CIDR routes are omitted do not require that flag. See [version-compatibility.md](version-compatibility.md).

```bash
chalk sandbox create \
  --name "$SANDBOX_NAME" \
  --image node:22-bookworm \
  --cpu 2 \
  --memory 4Gi \
  --lifetime 8h \
  --compute-class host \
  --no-routes \
  --chalk-identity \
  --secret-from-local-env GH_TOKEN \
  --resource-detection github-pull-requests \
  --host github.com \
  --host api.github.com
```

With the Python SDK, set the hostname policy, detector, and identity on the `Sandbox` itself. `NetworkPolicy(allowed_hosts=...)` allows ordinary hostname access without adding CIDR routes. `add_credentials` adds or updates the named API host with a credential-injection rule while preserving the other hostname entries; the sandbox receives a placeholder and the policy injects the real Chalk secret only for that host.

```python
from chalkcompute import (
    ComputeClass,
    NetworkPolicy,
    ResourceDetector,
    Sandbox,
    SandboxClient,
)

sandbox = Sandbox(
    image="node:22-bookworm",
    lifetime="1800s",
    compute_class=ComputeClass.HOST,
    network_policy=NetworkPolicy(
        allowed_hosts=["github.com"],
    ),
    chalk_identity=True,
    resource_detectors=[ResourceDetector.GITHUB_PULL_REQUESTS],
).add_credentials("api.github.com", "GH_TOKEN")

with SandboxClient.from_env() as client:
    client.run(sandbox, wait=True)
```

The example allows `github.com` for cloning or pushing over HTTPS and binds the `GH_TOKEN` credential only to `api.github.com`. Credential injection is hostname-scoped: a credential bound to `api.github.com` does not authenticate `git push` to `github.com`. For the simplest artifact workflow, use the API-only helper below and omit `github.com`. If the task truly requires Git transport, configure its authentication separately and verify it before making changes. Do not broaden credential scope or egress merely because one stage uses a second GitHub hostname.

Hostname rules are exact. Add other GitHub download hosts only when the workflow actually needs them. Tell the agent to use GitHub hostnames, not resolved IP addresses. Do not add `--route '0.0.0.0/0'` as a convenience to this sandbox while the precedence workaround is required.

Consult the release notes and re-test artifact creation end to end before changing this network requirement.

## Prove detection

Inside the sandbox, choose one complete publishing path rather than mixing their authentication assumptions:

1. **API-only path (recommended for artifact creation):** bind `GH_TOKEN` to `api.github.com`, preflight repository access, then use this skill's helper to create the branch, commit one file, and create the PR through GitHub GraphQL. This path needs only `api.github.com`.
2. **Git transport path:** allow `github.com`, configure and verify Git authentication for that hostname, clone or open the repository, create and push a branch, then create the PR with `gh pr create` or the helper. Binding a credential only to `api.github.com` does not authenticate the push.

In artifact mode, the final operation is constrained: use `gh pr create` or this skill's `create-github-pr.mjs`. Never call REST `POST /repos/{owner}/{repo}/pulls`. REST remains valid for repository reads, branch creation, and content updates; only pull-request creation must switch to GraphQL.

The default `node:22-bookworm` image may not include `gh`. Do not fall back to the REST pull-request endpoint. Either install `gh`, or use the reusable Node helper bundled with this skill:

```bash
GITHUB_REPOSITORY=OWNER/REPO \
EXPECTED_GITHUB_LOGIN=EXPECTED_LOGIN \
node /path/to/chalk-sandboxes/scripts/create-github-pr.mjs --check

GITHUB_REPOSITORY=OWNER/REPO \
HEAD_REF="$BRANCH" \
BASE_REF=main \
PR_TITLE="Sandbox artifact canary" \
CHANGE_PATH=".chalk-artifacts/$BRANCH.txt" \
CHANGE_CONTENT="Created by Chalk sandbox $HOSTNAME" \
COMMIT_MESSAGE="Add sandbox artifact canary" \
node /path/to/chalk-sandboxes/scripts/create-github-pr.mjs --create-change
```

The helper reads `GH_TOKEN`. In `--check` mode it verifies the authenticated login and repository access, printing only non-secret identity metadata. With `--create-change`, it creates `HEAD_REF` from `BASE_REF` when necessary, commits `CHANGE_CONTENT` at `CHANGE_PATH`, performs the detectable GraphQL `createPullRequest` mutation, and prints only the resulting PR URL. Without `--create-change`, it assumes the branch already exists and only creates the PR. It fails on an unexpected login, inaccessible repository, invalid path, GraphQL error, or HTTP error. Use a unique `HEAD_REF` and `CHANGE_PATH` for each run. Agents may implement the same GraphQL exchange directly when the skill directory is not available at runtime.

An authenticated `/user` or `viewer` response is not enough: a token for the wrong GitHub account can authenticate successfully while a private target repository returns 404. Stop on an unexpected login or inaccessible repository. Do not create a substitute repository, switch to an ambient local credential, or continue a multi-sandbox run until the configured Chalk secret is corrected.

For a multi-sandbox run, prefer a dedicated run-scoped secret or immutable credential reference when the platform supports it. Run the login-and-repository preflight in every sandbox, and stop creating queued sandboxes as soon as the observed login changes or repository access fails.

Then inspect the sandbox's **Artifacts** tab or the sandbox-resources API. Record the sandbox ID and PR URL so missing detection can be reproduced.

If the PR exists but the artifact list is empty, verify all of the following before contacting Chalk Support:

- `--resource-detection github-pull-requests` was present in the create request.
- Chalk workload identity was enabled (`--chalk-identity` in the CLI, or its SDK equivalent).
- `api.github.com` was in the hostname policy.
- No `--route` overlapped the GitHub destination.
- The PR creation request was GraphQL `createPullRequest`, not REST `POST /repos/{owner}/{repo}/pulls`.
- The PR was created after the sandbox was running, from traffic originating in that sandbox.
- The target Chalk environment supports GitHub pull-request detection for sandboxes.

Before an important or multi-sandbox run, check for compatible CLI and SDK updates, record the exact installed versions or build identifiers, and pass a canary using [version-compatibility.md](version-compatibility.md). If the installed CLI lacks `--resource-detection` or `--host`, obtain authorization to update it rather than silently falling back to an undetectable configuration.
