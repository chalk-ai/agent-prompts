# Chalk Development Guidelines

Rules for writing correct Chalk code. When something here is not enough, fetch the docs:

- Quick reference: https://docs.chalk.ai/llms.txt (link index by product area)
- Full documentation: https://docs.chalk.ai/llms-full.txt
- Any docs page as Markdown: append `.md` to its URL, e.g. https://docs.chalk.ai/docs/compute/sandbox.md
- Examples: https://github.com/chalk-ai/examples

## What is Chalk?

Chalk is a feature engine plus a compute platform. The feature engine serves low-latency online features and point-in-time-correct offline datasets from pipelines written in Python and SQL. Alongside it, Chalk runs arbitrary code as **Sandboxes** (isolated ephemeral tasks), **Scaling Groups** (autoscaling HTTP services), and **Functions** (remote Python callables), plus **Evaluations** for scoring LLM workloads and a **Model Gateway** for routing LLM traffic.

Two SDKs: `chalkpy` (`chalk.*`, features and resolvers) and `chalkcompute` (sandboxes, scaling groups, functions, evaluations). Install `chalkcompute` separately with `pip install chalkcompute`.

---

# Part 1: Features and Resolvers (`chalkpy`)

## Feature classes

```python
from datetime import datetime
from chalk.features import features, feature, DataFrame, Primary

@features
class Transaction:
    id: str
    user_id: "User.id"          # foreign key; quotes allow forward references
    amount: float
    created_at: datetime

@features
class User:
    id: str                     # primary key (implicit when named `id`; else Primary[T])
    name: str
    email: str
    transactions: DataFrame[Transaction]   # has-many via the foreign key above
    risk_score: float = feature(max_staleness="1d", default=0.5)
```

- Features are namespaced by class: `user.name`, `transaction.amount`.
- Types: `int`, `str`, `float`, `bool`, `datetime`, `Optional[T]`, `list[T]`, `Vector`, pydantic structs.
- Has-one: `profile: Profile` with `user_id: "User.id"` on `Profile`. Optional: `profile: "Profile | None"`.
- Prefer implicit joins via `"User.id"` annotations over explicit `has_one()` / `has_many()`.
- Forward references: quote the **whole** annotation, `"DataFrame[Transaction]"`, never `DataFrame["Transaction"]`.
- Cross-file cycles: import under `TYPE_CHECKING` and use string references.
- Annotate ownership: `@features(owner="team@co.com", tags=["group:risk"])`; per-feature `# :owner:` / `# :tags:` comments.

## Expressions (preferred for low latency)

```python
import chalk.functions as F
from chalk.features import _

@features
class User:
    id: str
    name: str
    email: str
    transactions: DataFrame[Transaction]

    txn_count: int = _.transactions.count()
    total_spent: float = _.transactions[_.amount].sum()
    large_txns: int = _.transactions[_.amount > 1000].count()
    email_domain: str = F.lower(F.split_part(_.email, "@", 2))
```

Expressions compile to native code. Use them instead of Python resolvers whenever the logic is arithmetic, comparisons, string ops, or DataFrame filter/project/aggregate. `_` is the current class; `chalk.functions` (`F.*`) holds the builtins.

## Windowed aggregations

```python
from chalk import windowed, Windowed

spend: Windowed[float] = windowed(
    "1d", "7d", "30d",
    expression=_.transactions[
        _.amount,
        _.created_at > _.chalk_window,   # window start, adapts to each size
        _.created_at < _.chalk_now,
    ].sum(),
    default=0,
    materialization={"bucket_durations": {"1h": ["1d"], "1d": ["7d", "30d"]}},  # optional, for high volume
)
```

Use `_.chalk_window` inside `windowed()`; use `_.chalk_now - timedelta(...)` only for fixed offsets outside it.

## Resolvers

**SQL file resolvers** (`*.chalk.sql`) are the default for loading data:

```sql
-- resolves: User
-- source: pg_users
-- type: online            -- or offline | streaming
-- count: one              -- one | one_or_none | all
-- incremental:            -- offline, append-only tables only
--   mode: row
--   lookback_period: 60m
--   incremental_column: created_at
select id, name, email from users where id = ${user.id}
```

Name sources descriptively (not `postgres`). Select explicit columns, never `select *`. Generate programmatically with `make_sql_file_resolver(name=, source=, resolves=, query=)`.

**Python resolvers:**

```python
from chalk import online, offline
from chalk.features import Features, DataFrame

@online
def email_domain(email: User.email) -> User.email_domain:
    return email.split("@")[1].lower()

@online
def profile_bits(uid: User.id) -> Features[User.name, User.email]:
    ...

@offline
def credit_scores() -> DataFrame[User.id, User.credit_score]:
    return DataFrame.read_csv("scores.csv")
```

- Inputs and outputs must share one root namespace.
- Return a scalar, `Features[...]`, a feature-class instance, or a `DataFrame`.
- `@online` for request time, `@offline` for batch and cron (`@offline(cron="0 0 * * *")`).

**Static resolvers** run once at deploy to build a plan over external files, using `chalkdf`:

```python
@online(static=True)
def reference_data() -> DataFrame[Rate.id, Rate.value]:
    from chalkdf import DataFrame
    return DataFrame.scan(["s3://bucket/rates/*.parquet"]).select(
        _.id.alias("rate.id"), _.value.alias("rate.value"),
    )
```

**Stream resolvers** (Kafka, Kinesis, PubSub):

```python
from pydantic import BaseModel
from chalk.features.resolver import make_stream_resolver
from chalk.streams import KafkaSource
import chalk.functions as F

class TxnMessage(BaseModel):
    id: str
    user_id: str
    amount: float
    ts_ms: int

txn_stream = make_stream_resolver(
    name="txn_stream",
    source=KafkaSource(name="transactions_topic"),
    message_type=TxnMessage,
    output_features={
        Transaction.id: _.id,
        Transaction.user_id: _.user_id,
        Transaction.amount: _.amount,
        Transaction.created_at: F.from_unix_seconds(_.ts_ms / 1000),
    },
)
```

For non-JSON payloads pass `parse=`: `F.avro_deserialize(_, schema=..., target_type=Msg)` or `F.proto_deserialize(_, Msg)`, wrapped in `F.recover(expr, None)` to drop unparseable messages. Returning `None` from `parse` skips the message.

## Queries

```python
from chalk.client import ChalkClient

client = ChalkClient()
client.query(input={User.id: "u1"}, output=[User.name, User.spend["7d"]], staleness={User.risk_score: "1h"})

ds = client.offline_query(
    input={User.id: ids}, output=[User.risk_score], dataset_name="train_v3", recompute_features=True,
)
df = ds.to_pandas()
```

## Deploy and branch

```bash
chalk apply --branch my-feature        # deploy to a branch
chalk query --branch my-feature --in user.id=u1 --out user.name
chalk apply                            # production
```

## Model registry

```python
from chalk.ml import ModelReference
from chalk import make_model_resolver

risk_model = ModelReference.from_alias(name="RiskModel", alias="production")
make_model_resolver(name="risk", model=risk_model, input=[User.age, User.income], output=[User.risk_score])
```

Register versions outside deployment code with `ChalkClient().register_model_version(name=, aliases=, model=|model_paths=, input_features=, output_features=)`. Versioned outputs use `User.risk_score@2` with `feature(versions=2)`.

## Model Gateway

- Call LLMs from Functions through the Model Gateway, and evaluate outputs with Evaluations (Part 3).
- The **Model Gateway** is an OpenAI-compatible router over OpenAI, Anthropic, and Gemini with keys, budgets, rate limits, and fallback managed in the dashboard. Base URL `https://api.chalk.ai/v1/router` (your API server host on dedicated deployments); headers `Authorization: Bearer <ROUTER_API_KEY>` and `X-Chalk-Env-Id: <env>`. It also serves the Anthropic Messages API, so Claude Code and Anthropic SDKs point at it directly. Model names carry a connection prefix, e.g. `openai/gpt-5`; list them with `chalk router model list`.

---

# Part 2: Sandboxes, Scaling Groups, and Functions (`chalkcompute`)

## Choosing a primitive

| Shape | Use | Example |
|---|---|---|
| One ephemeral or long-running task you drive with `exec` | `Sandbox` | Run agent-generated code, a backfill, a per-session workspace, a multi-day training job |
| A replicated HTTP/gRPC service that autoscales | `ScalingGroup` | vLLM server, internal API, chat or agent backend, hosted MCP server |
| A remote Python call, visible to the rest of Chalk | `Function` | Embedding or re-ranking, enrichment inside a resolver, "score this row" |

Pick by how the workload is reached, not by how long it runs. A function is deployed onto a scaling group and registered in the Chalk Catalog, so resolvers, Chalk SQL, and DataFrames can call it. All three share the same `image`, `cpu`/`memory`/`gpu`, `env`, `secrets`, `volumes`, `tags`, and `chalk_identity` settings.

## Shared runtime settings

**Images** are content-addressed and cached; a repeat spec is never rebuilt.

```python
from chalkcompute import Image

img = (
    Image.debian_slim("3.12")                       # python:<v>-slim-bookworm; also Image.base("node:22-slim"), Image.from_dockerfile(path)
    .pip_install(["requests", "pandas>=2.0"])       # or .pip_install_from_requirements("requirements.txt")
    .run_commands("apt-get update && apt-get install -y ffmpeg")
    .add_local_file("./app.py", "/app/app.py")      # uploaded at start; strategy="copy" bakes it in
    .add_local_dir("./src", "/app/src")
    .add_local_python_source("helpers", "mypkg")    # importable local modules for functions
    .workdir("/app")
    .env({"LOG_LEVEL": "INFO"})
    .entrypoint(["python", "/app/app.py"])
)
```

**Resources:** `cpu` must be `250m`, `500m`, or a power of two (`1`, `2`, `4`, ...). `memory` must be at least 2Gi per core (`512Mi` for `250m`, `2Gi` for `1`, `4Gi` for `2`). Defaults are `250m` and `512Mi`. `gpu` takes `"nvidia-l4:2"`, `"nvidia-l4"`, or `"2"`; types are GKE accelerator names (`nvidia-tesla-t4`, `nvidia-l4`, `nvidia-a100-80gb`, `nvidia-h100-80gb`, ...).

**Secrets** resolve at deploy time into environment variables. Never put credentials in source or images.

```python
from chalkcompute import Secret

Secret.from_chalk_env("OPENAI_API_KEY", alias="API_KEY")            # one named Chalk secret (`chalk secret set NAME`)
Secret.from_chalk_integration("prod_postgres", prefix="PG_", keys=["host", "password"])
Secret.from_local_env("OPENAI_API_KEY")                             # dev loop: upsert from your shell at deploy time
Secret.from_local_env_file(".env")
```

`Secret.from_env` / `from_integration` are deprecated aliases.

**Volumes** are durable, environment-scoped storage mounted as `volumes=[("name", "/mount")]`. `Volume("name")` creates one if missing; `vol.put_file(path, bytes)` uploads. CLI: `chalk volume create chalk://name`, `chalk volume cp --recursive`. Shared across replicas and sandboxes; last writer wins, so treat as read-mostly.

## Sandboxes

gVisor-isolated containers with private filesystem, network, and PID namespaces. Default `compute_class=ComputeClass.HOST` (managed serverless, sub-2s cold start). `ComputeClass.K8S` runs on your cluster but loses `exec`, network policies, resource detection, managed SSH, and tunnels, and gains `KernelPolicy.OPEN`.

```python
from chalkcompute import Image, Sandbox, Secret, Volume, NetworkPolicy, RestartPolicy

sandbox = Sandbox(
    image=Image.debian_slim("3.12").pip_install(["requests"]).add_local_file("./script.py", "/app/script.py"),
    name="hello-sandbox",                      # lets you Sandbox.from_name() later
    cpu="1",
    memory="2Gi",
    lifetime="3600s",                          # protobuf duration: seconds + "s"; omit = run until terminated. "1h" is invalid, "0s" raises
    env={"LOG_LEVEL": "INFO"},
    secrets=[Secret.from_chalk_env("OPENAI_API_KEY")],
    volumes=[("training-data", "/data")],
    network_policy=NetworkPolicy(allowed_hosts=["api.openai.com", "pypi.org", "*.github.com", "github.com"]),
    restart_policy=RestartPolicy.ALWAYS,       # recreate on node drain/preemption; default NEVER
    chalk_identity=True,                       # workload identity; required to use the MCP Gateway without a token
    tags={"team": "ml"},
).run()                                        # builds image, creates sandbox, waits until running

result = sandbox.exec("python", "/app/script.py", timeout_secs=300, workdir="/app", env={"X": "1"})
print(result.stdout_text, result.stderr_text, result.exit_code)

info = sandbox.refresh()                       # SandboxInfo; status in pending|running|succeeded|failed...
sandbox.terminate(grace_period_seconds=30)
```

- `exec` blocks; `exec_stream(...)` streams output; `exec_start(...)` gives stdin and signals. All three need `host`. On `k8s`, pass the command as `entrypoint=` at creation.
- Reattach from another process: `Sandbox.from_id(id)`, `Sandbox.from_name(name)`, `Sandbox.list_all()`.
- Long jobs: start detached (`sandbox.exec("sh", "-c", "nohup python train.py > /checkpoints/log 2>&1 &")`), write progress to a volume, reattach later. A recreated sandbox starts from a clean image, so resume from the volume. Lifetime expiry stops the sandbox without draining.
- Billing is per provisioned resource for as long as the sandbox exists. Always terminate when done.
- Networking: outbound is open by default on `host`; `NetworkPolicy(allowed_hosts=...)` restricts it. One policy per sandbox (`network_policy`, singular). `allowed_routes=[NetworkPolicy.Route("10.0.0.0/8", ports=[(5432, 5432)])]` and `denied_routes=["169.254.169.254/32"]` for CIDRs; denied wins. `allowed_hosts` as a `{host: [NetworkPolicy.Rule(...)]}` mapping rewrites matching requests, e.g. injecting a Secret into an auth header via `Transformer(headers=..., headers_secrets=...)`, so the sandbox never sees the credential. `Sandbox(...).add_credentials(hosts, secret).run()` is the shorthand.
- Managed SSH: `managed_ssh={"github": SshDest("github.com", "GITHUB_DEPLOY_KEY", username="git")}` loads a secret key into an agent the sandbox can sign with but not read. Images containing `/root/.ssh` are rejected.
- No sandbox-to-sandbox networking. Share data through a volume or an external service. Reach a sandbox from your machine over SSH with Tunnels (`chalk sandbox configure-ssh`, then `ssh chalk-sbx-<name>`); sandboxes accept no inbound connections otherwise.
- `security_policy=ContainerSecurityPolicy(kernel_policy=KernelPolicy.RESTRICTED)` is the default; `OPEN` only on `k8s`.
- `resource_detectors=[ResourceDetector.GITHUB_PULL_REQUESTS]` records PRs the sandbox opens (adds `api.github.com` to the policy).
- GPU sandboxes: `gpu="nvidia-l4"` with `compute_class=ComputeClass.K8S`, or on `host` when a host pool with that GPU exists.
- MCP Gateway: sandboxes call your org's MCP servers through a proxy that holds the real credentials. With `chalk_identity=True` no token is needed; otherwise send a Chalk access token and `X-Chalk-Env-Id`.

**Coding agent sandbox** (Claude Code in a fresh checkout):

```python
@chalkcompute.function(secrets=[chalkcompute.Secret.from_chalk_env("GITHUB_TOKEN")], chalk_identity=True)
def coding_agent_sandbox(memory_gib: int = 16) -> chalkcompute.Sandbox:
    sandbox = (
        chalkcompute.Sandbox(
            name="coding-agent-" + str(uuid.uuid4()),
            image=chalkcompute.Image.sandbox()
                .with_claude_code("2.1.270", trusted_dirs=["/workspace/repo"], permission_mode="bypassPermissions")
                .with_gh().with_tmux().workdir("/workspace/repo"),
            cpu="2", memory=f"{memory_gib}Gi",
            chalk_identity=True,
            env=chalkcompute.GitConfig(name="Your Name", email="you@example.com"),
            resource_detectors=[chalkcompute.ResourceDetector.GITHUB_PULL_REQUESTS],
        )
        .add_credentials(["api.github.com", "uploads.github.com"], chalkcompute.Secret.from_chalk_env("GITHUB_TOKEN", alias="GH_TOKEN"))
        .add_credentials("api.anthropic.com", chalkcompute.Secret.from_chalk_env("CLAUDE_CODE_OAUTH_TOKEN"))
        .run()
    )
    sandbox.git.clone(url="https://github.com/org/repo.git", path="/workspace/repo", branch="main")
    return sandbox
```

```bash
chalk sandbox start coding_agent_sandbox --arg memory_gib=32 --tmux -- claude   # provision + launch
chalk sandbox attach                                                           # reconnect after Ctrl-B D
chalk sandbox terminate --id <sandbox-id>
chalk sandbox create -i python:3.12 -n pr-bot --host api.github.com --resource-detection github-pull-requests
```

## Scaling Groups

A Kubernetes Deployment with rolling updates, a TLS DNS name, and autoscaling on CPU, GPU, schedule, or (for functions) queue depth. Scales to zero when `min_replicas=0`.

```python
import chalkcompute

img = (
    chalkcompute.Image.debian_slim("3.12")
    .pip_install(["flask"])
    .add_local_file("./app.py", "/app/app.py")
    .entrypoint(["python", "/app/app.py"])           # app must bind 0.0.0.0:<port>
)

sg = chalkcompute.ScalingGroup(
    image=img,                                         # or a registry string "my-registry/img:v1"
    name="hello-api",
    port=8080,
    min_replicas=1,                                    # default 1; 0 enables scale-to-zero
    max_replicas=3,                                    # default 1
    cpu="2", memory="4Gi", gpu=None,
    env={"LOG_LEVEL": "INFO"},
    secrets=[chalkcompute.Secret.from_chalk_env("API_TOKEN")],
    volumes=[("model-weights", "/models")],
    entrypoint=["python", "/app/app.py"],              # overrides the image entrypoint
    protocol="http",                                   # "grpc" to route gRPC
    startup_probe=chalkcompute.StartupProbe.http("/health"),
    target_cpu_utilization_percentage=70,
    shutdown_delay_seconds=30,
    scale_from_zero_request_policy=chalkcompute.ScaleFromZeroRequestPolicy.HOLD,   # default returns 503 while scaling up
    cron_scaling_trigger=chalkcompute.CronScalingTrigger(
        timezone="America/New_York",
        windows=[chalkcompute.CronScalingWindow(start="0 8 * * 1-5", end="0 18 * * 1-5", desired_replicas=3)],
    ),
).deploy()                                             # builds, uploads, waits for a ready replica (and the probe)

print(sg.web_url)                                      # https://<env-id>-hello-api.<gateway-domain>
resp = sg.call("/predict", method="POST", json={"text": "hi"}, timeout=60.0)   # ad-hoc; send prod traffic to web_url
resp.status, resp.json(), resp.text()

info = sg.refresh()                                    # status: Pending | Available | Degraded | ScaledToZero | Unhealthy | Error
info.ready_replicas, info.available_replicas

sg = chalkcompute.ScalingGroup.from_name("hello-api")  # or .from_id(...)
sg.delete()
```

**Revisions:** every `deploy()` under the same name records an immutable revision and routes traffic to it, keeping the same ID, name, and URL. `sg.revisions()` lists newest first (`is_current`, `Superseded`); `sg.rollback(revision_id, wait_ready=True, timeout=300)` selects an earlier one without recording a new revision.

**CLI** mirrors the SDK and shares the same objects:

```bash
chalk scaling-group create --image=my-registry/llm:v3 --name=llm-inference --port=8080 \
  --min-replicas=2 --max-replicas=4 --target-cpu=70 --gpu=nvidia-l4:1 --cpu=8 --memory=32Gi \
  --volume=chalk://llama3-weights=/models/llama3 --secret=HF_TOKEN --env=LOG_LEVEL=INFO \
  --tags="model=llama3,team=ml" --entrypoint="python,-m,vllm.entrypoints.openai.api_server,--model,/models/llama3"
chalk scaling-group create ... --min-replicas=0 --scale-from-zero-request-policy=hold
chalk scaling-group create ... --no-traffic            # record a revision without serving it
chalk scaling-group list | get --name=X | delete --name=X
chalk scaling-group revisions list --name=X
chalk scaling-group update-traffic --name=X --revision-id=<id>   # roll back or promote
```

`--max-replicas` is required on the CLI. `--entrypoint` is comma-separated; the SDK takes a list. `--secret` also accepts `integration:NAME[:KEY1,KEY2]`. Scheduled replica floors are SDK-only. Request a token on the public hostname via workload-settings Authentication.

## Functions

A Python callable deployed onto a scaling group and registered in the Chalk Catalog. Calls go through a durable Function Queue that enforces concurrency, rate limits, and retries, so workers can scale or restart without losing in-flight calls.

```python
import chalkcompute

@chalkcompute.function(
    image=chalkcompute.Image.debian_slim("3.12").pip_install(["sentence-transformers"]),
    cpu="2", memory="4Gi", gpu="nvidia-l4",
    secrets=[chalkcompute.Secret.from_chalk_env("HF_TOKEN")],
    volumes=[("model-cache", "/models")],
    retries=3,                                 # transient gRPC failures only; function exceptions are terminal
    concurrency=4,                             # in-flight cap, default 1; share via key=
    rate_limit=..., max_batching_size=25, max_buffer_duration=2000,
    min_replicas=0, max_replicas=8, target_queue_depth=5, target_cpu_utilization_percentage=70,
    scaling_interval_seconds=30, shutdown_delay_seconds=120,
)
def embed(texts: list[str]) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer   # heavy imports inside the body
    return SentenceTransformer("all-MiniLM-L6-v2").encode(texts).tolist()

if __name__ == "__main__":
    embed.wait_ready()
    print(embed.remote(["hello"]))             # embed([...]) would run locally
```

- Deployment happens when the decorated module is imported, in the background. Every import deploys a new version, so keep uploads, runs, and submissions under `if __name__ == "__main__":`. Attach without redeploying: `RemoteFunction.from_name("embed")` or `from_version_id(id)`.
- Helpers in the function's own top-level package ship automatically when run as a package (`python -m pkg.mod` or via an entrypoint). Running a file by path ships only that file; declare other local code with `Image.add_local_python_source(...)`. Third-party deps still go in `pip_install`.
- Async: `handle = fn.defer(*args)`, `handle.get(timeout=5.0)` (TimeoutError leaves the call running), `chalkcompute.gather(handles, timeout=30.0)` for ordered fan-out (awaitable inside an event loop). Failures raise `chalkcompute.FunctionError` / `RuntimeError` carrying the original type and message.
- Streaming: an `async def` generator yields results incrementally; the caller iterates `async for x in fn.remote(...)`. Functions can call other functions with `.remote()` to compose pipelines.
- Scheduling: `@chalkcompute.function(schedule="0 2 * * *")` or `schedule="15m"` (`m` ≤ 60, `h` ≤ 24, `1d`; UTC; clock-aligned). Scheduled functions take no arguments and cannot yield. Log with `chalk_logger`.
- From features: in a static resolver, `F.catalog_call("function.extract_entities", _.memo)` applies a function per row (qualified names are `function.<name>` or `model.<name>`). Only takes effect after `chalk apply`. Discover targets in `chalk.information_schema.compute_functions` / `.models`.
- `chalkcompute.get_call_context()` returns the call headers inside a function (empty outside Chalk).

## Workflows (private preview)

Multi-step jobs: `@task` functions orchestrated by an `async def @workflow` from `chalk.workflows`. `chalkcompute.deploy_workflow_worker(name=..., cpu=, memory=, max_replicas=)` packages the script (declare shared deps in a `# /// script` block incl. `chalkpy[workflows]`) and creates scale-to-zero scaling groups, one per distinct `@task(image=...)`. Submit with `ChalkClient().trigger_workflow("score_batch", inputs, task_queue="chalkcompute-workflows.<name>", wait=False)` or `worker.trigger(...)`. Workflow bodies must be deterministic; put I/O, randomness, and clocks in tasks. `task.submit()` + `asyncio.gather` runs tasks in parallel. `@task(retries=2, timeout="5m", retry_delay="30s")`; make external writes idempotent.

---

# Part 3: Evaluations

An evaluation pins a **dataset revision**, one **task**, and one or more **scorers**; it is immutable. Running it calls the task once per row, passes each output to every scorer, and writes a result dataset. Evaluations are offline. Ask Chalk support to enable Evaluations and the async Function queue first; a new environment also needs an active deployment.

```python
import chalkcompute
import pyarrow.compute as pc

@chalkcompute.function
def triage(message: str) -> str:            # parameters bind to dataset columns by name
    ...
    return json.dumps({"intent": intent, "amount": amount})

@chalkcompute.function
def amount_is_plausible(message: str, output: str) -> float:   # `output` = the task's return value
    ...
    return 1.0

valid_json = chalkcompute.scorers.json_valid(name="valid-json")   # built-ins are called at top level; calling deploys them
intent_match = chalkcompute.scorers.json_match(inputs=("output", "expected"), paths=["intent"], name="intent-match")

if __name__ == "__main__":
    dataset = chalkcompute.DatasetClient().upload("support-triage", [
        {"message": "...", "expected": '{"intent": "refund", "amount": 49.99}', "expected_intent": "refund"},
    ])                                                   # also CSV/Parquet paths, Arrow tables, column dicts, dataframes

    suite = next((s for s in chalkcompute.EvaluationSuite.list_all() if s.name == "Release"), None) \
        or chalkcompute.EvaluationSuite.create("Release")  # names are unique per environment

    evaluation = chalkcompute.Evaluation.create(
        "support-triage",
        dataset=dataset,                                 # or DatasetRevisionRef(dataset_name="support-triage") -> latest, pinned at create
        task=triage,                                     # or RemoteFunction.from_name("triage") / from_version_id(...)
        scorers=[valid_json, intent_match, amount_is_plausible],
        metadata={"owner": "support-eng"},
        suite_id=suite.id,
    )

    run = evaluation.run(metadata={"git_sha": "abc123"}).wait(timeout=600.0, raise_on_failure=True)
    table = chalkcompute.DatasetClient().read(run.result_dataset)   # PyArrow table
    print(pc.mean(table["intent-match_value"]).as_py())
```

**Rules:**

- Definitions (task, scorers, built-in scorer calls) live at module top level; uploads, `Evaluation.create`, and `run()` go under `if __name__ == "__main__":`. Workers import the module.
- `Evaluation.create` waits for decorated functions to deploy; it never deploys an imperative `RemoteFunction` (raises `EvaluationError`) and rejects bare callables (`TypeError`). At least one scorer is required.
- Reserved dataset columns: `output`, `trace`, `__chalk_evaluation_output`, `__chalk_evaluation_row_id`, `__chalk__session_id__`, `__chalk__task_session_id__`.
- Result columns: the dataset's columns plus `output`, `output_error`, and per scorer `<name>_value`, `<name>_error`, `<name>_metadata`. Index with brackets (`table["intent-match_value"]`). A `SUCCEEDED` run can still have per-row errors; check the error columns.
- `run.wait()` raises `EvaluationRunFailedError` (with `.run`) or `TimeoutError`; a timed-out run keeps going, reattach with `EvaluationRun.from_id(id)`. Statuses: `PENDING`, `RUNNING`, `FINALIZING`, `SUCCEEDED`, `FAILED`, `CANCELED`.
- `run(execution_mode="workflow" | "on_read")`: default is `workflow` when the environment has an orchestrator.
- Inside a task or scorer, `chalkcompute.get_call_context()` exposes `x-chalk-evaluation-id` and `x-chalk-evaluation-run-id`.

**Scorers** return a float in [0, 1], an `EvaluationScorerResult(score=, metadata=)`, or a list of them (several scores from one computation; empty list emits nothing). Return the dataclass, not a dict.

| Scorer | Measures | Notes |
|---|---|---|
| `exact_match` | same text | `normalize_whitespace`, `case_insensitive` |
| `contains` | expected substrings present | `mode="all" / "any" / "fraction"` |
| `regex_match` | pattern matches | pattern positional; `full_match`; named groups → metadata |
| `levenshtein` | normalized edit distance | |
| `string_similarity` | named measure | `"jaro_winkler"`, `"jaccard"`, `"token_set"`, `"token_sort"`, `"partial"`, `"sequence"` |
| `json_valid` | parses as JSON | reads `("output",)` |
| `json_match` | JSON documents agree | `paths=["intent", "user.id"]` → share of agreeing paths |
| `numeric_close` | numbers agree | `tolerance`, `relative` |
| `embedding_similarity` | same meaning | `model` (default `text-embedding-3-small`), `base_url`, `api_key` |
| `llm_judge` | a quality described by a pydantic model | see below |
| `jev` | label chosen by a model, escalated when unsure | see below |
| `sql` | Chalk SQL expression over row columns | deploys nothing |

Defaults: `inputs=("output", "expected")` for two-column scorers, `("output",)` for `regex_match` and `json_valid`. Extra kwargs go to `@chalkcompute.function` (image, resources, retries).

```python
from pydantic import BaseModel, Field

class TraceQuality(BaseModel):
    score: float = Field(ge=0, le=1)          # required: becomes the score; other fields become metadata
    reason: str

judge = chalkcompute.scorers.llm_judge(TraceQuality, model="openai/gpt-5", instructions="Grade the agent's login attempt.",
                                       inputs=("output",), completion_kwargs={"temperature": 0})
# deploys as "trace-quality"; with no api_key/base_url it calls the Model Gateway as the environment

intent_judge = chalkcompute.scorers.jev(
    labels={"correct": 1.0, "partial": 0.5, "wrong": 0.0},
    rubric="correct: ...; partial: ...; wrong: anything else.",
    model="openai/gpt-5-mini", fallback_model="openai/gpt-5", threshold=0.9,
    inputs=("message", "output"), name="intent-judge",
)

answered = chalkcompute.scorers.sql("answered", '''CASE WHEN "output" IS NOT NULL AND "output" <> '' THEN 1.0 ELSE 0.0 END''')
```

Quote column names in SQL scorers. Judges are generated at import time, so sibling functions in the same file need `pydantic` in their image, and judges cannot deploy in strip mode.

**Rescoring** reuses a finished run's task outputs and calls only what changed: `run.rescore(scorers=[...], name=...)` creates a new evaluation (default `"<name> (rescored)"`) on the same dataset revision, task, and suite; `run.rescore(evaluation=other)` scores into an existing evaluation that uses the same dataset revision; `run.rescore()` with no arguments only retries scorers that failed. Only succeeded runs can be rescored.

**Other handles:** `Evaluation.from_id`, `evaluation.refresh()`, `evaluation.runs(limit=)`, `EvaluationRun.from_id`, `suite.evaluations()`, `suite.runs()`. Hold one `chalkcompute.EvaluationClient(chalk_client=...)` for many calls (`create_suite`, `create`, `run`, `list`, `list_runs`, `rescore`). Errors: `EvaluationError` (`.status_code`), `EvaluationNotFoundError`, `EvaluationRunFailedError`.

The dashboard (**Evaluations** in the sidebar) shows suites, runs, per-scorer averages, cost and tokens, run comparison, a baseline run, **Edit scorers** (copies the evaluation and rescores), and **Quick evaluation** for creating an evaluation from an LLM prompt template (`{{column}}`), a SQL expression, or a deployed function without code.

---

# Pitfalls to avoid

- Writing a Python resolver for logic an expression can do. Expressions first.
- `select *` in SQL resolvers; mixing namespaces in one resolver; `DataFrame["Transaction"]` instead of `"DataFrame[Transaction]"`.
- Manual `timedelta` filters inside `windowed()`; use `_.chalk_window`.
- `lifetime="1h"` on a sandbox (must be `"3600s"`); a list for `network_policy`; forgetting to `terminate()`; expecting `exec` on `k8s`.
- Memory below 2Gi per core, or a CPU value that is not `250m`, `500m`, or a power of two.
- Omitting `--max-replicas` on `chalk scaling-group create`; a server bound to `127.0.0.1` instead of `0.0.0.0`.
- Top-level side effects (uploads, `run()`, `deploy_workflow_worker`) outside `if __name__ == "__main__":` in any module that defines functions, scorers, or workflows.
- Treating a `SUCCEEDED` evaluation run as a pass without reading `output_error` and `<scorer>_error`.
- Dataset columns named `output` or `trace`, or any `__chalk*` column, in an evaluation dataset.
- Hard-coding credentials anywhere a `Secret` would do.
