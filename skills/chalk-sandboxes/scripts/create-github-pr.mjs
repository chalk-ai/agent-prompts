#!/usr/bin/env node

const required = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
};

const token = required("GH_TOKEN");
const repository = required("GITHUB_REPOSITORY");
const checkOnly = process.argv.includes("--check");
const createChange = process.argv.includes("--create-change");
const expectedLogin = process.env.EXPECTED_GITHUB_LOGIN;
const [owner, name, extra] = repository.split("/");
if (!owner || !name || extra) {
  throw new Error("GITHUB_REPOSITORY must be OWNER/REPO");
}

async function graphql(query, variables, operationName) {
  const response = await fetch("https://api.github.com/graphql", {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "chalk-sandbox-resource-detection",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ query, variables, operationName }),
  });
  const payload = await response.json();
  if (!response.ok || payload.errors?.length) {
    const detail = payload.errors?.map((error) => error.message).join("; ") || JSON.stringify(payload);
    throw new Error(`GitHub GraphQL request failed (${response.status}): ${detail}`);
  }
  return payload.data;
}

const access = await graphql(
  `query RepositoryAccess($owner: String!, $name: String!) {
    viewer { login }
    repository(owner: $owner, name: $name) { id }
  }`,
  { owner, name },
  "RepositoryAccess",
);

const login = access.viewer.login;
if (expectedLogin && login.toLowerCase() !== expectedLogin.toLowerCase()) {
  throw new Error(`GitHub login mismatch: expected ${expectedLogin}, got ${login}`);
}
if (checkOnly) {
  console.log(JSON.stringify({ login, repository, accessible: true }));
  process.exit(0);
}

const headRefName = required("HEAD_REF");
const baseRefName = process.env.BASE_REF || "main";
const title = required("PR_TITLE");
const body = process.env.PR_BODY || "";

if (createChange) {
  if (headRefName.startsWith("refs/") || headRefName === baseRefName) {
    throw new Error("HEAD_REF must be a branch name distinct from BASE_REF, without a refs/ prefix");
  }

  const changePath = required("CHANGE_PATH");
  const changeContent = required("CHANGE_CONTENT");
  const commitMessage = process.env.COMMIT_MESSAGE || title;
  if (changePath.startsWith("/") || changePath.split("/").includes("..")) {
    throw new Error("CHANGE_PATH must be a repository-relative path without '..' segments");
  }

  const refs = await graphql(
    `query BranchRefs($owner: String!, $name: String!, $base: String!, $head: String!) {
      repository(owner: $owner, name: $name) {
        base: ref(qualifiedName: $base) { target { oid } }
        head: ref(qualifiedName: $head) { target { oid } }
      }
    }`,
    {
      owner,
      name,
      base: `refs/heads/${baseRefName}`,
      head: `refs/heads/${headRefName}`,
    },
    "BranchRefs",
  );

  const baseOid = refs.repository.base?.target?.oid;
  if (!baseOid) throw new Error(`Base branch not found: ${baseRefName}`);

  let headOid = refs.repository.head?.target?.oid;
  if (!headOid) {
    const ref = await graphql(
      `mutation CreateArtifactBranch($input: CreateRefInput!) {
        createRef(input: $input) { ref { target { oid } } }
      }`,
      {
        input: {
          repositoryId: access.repository.id,
          name: `refs/heads/${headRefName}`,
          oid: baseOid,
        },
      },
      "CreateArtifactBranch",
    );
    headOid = ref.createRef.ref.target.oid;
  }

  const commit = await graphql(
    `mutation CommitArtifactChange($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }`,
    {
      input: {
        branch: {
          repositoryNameWithOwner: repository,
          branchName: headRefName,
        },
        message: { headline: commitMessage },
        fileChanges: {
          additions: [
            {
              path: changePath,
              contents: Buffer.from(changeContent, "utf8").toString("base64"),
            },
          ],
        },
        expectedHeadOid: headOid,
      },
    },
    "CommitArtifactChange",
  );

  if (!commit.createCommitOnBranch.commit.oid) {
    throw new Error("GitHub did not return the created commit OID");
  }
}

const created = await graphql(
  `mutation createPullRequest($input: CreatePullRequestInput!) {
    createPullRequest(input: $input) {
      pullRequest { url }
    }
  }`,
  {
    input: {
      repositoryId: access.repository.id,
      baseRefName,
      headRefName,
      title,
      body,
    },
  },
  "createPullRequest",
);

console.log(created.createPullRequest.pullRequest.url);
