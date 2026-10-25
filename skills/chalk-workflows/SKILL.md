---
name: chalk-workflows
description: Define, run, deploy, and debug durable Python workflows with chalk.workflows @workflow and @task, including retries, parallel tasks, worker queues, and execution history. Distinguish them from Chalk offline job workflows and Argo image builds.
---

# Durable Python workflows

Use `from chalk.workflows import task, workflow` for durable Python orchestration. The environment's Workflow Orchestrator records execution history and coordinates tasks. These are separate from metaplanned offline queries and Argo image builds.

## Environment and prerequisites

Work from the user's Chalk project directory. CLI/SDK credentials can be scoped by directory in `~/.chalk.yml`; invoking a command from another directory can select another team's credentials even when `--environment` is explicit. Verify the intended environment with `chalk environment config --json`. Keep secrets out of command output.

Install `chalkpy[workflows]` in the project's Python environment, for example:

```bash
uv pip install --python .venv/bin/python 'chalkpy[workflows]'
```

Remote execution requires a Workflow Orchestrator Engine enabled for that environment. `failed_precondition: environment has no workflow orchestrator engine deployment` is an infrastructure prerequisite, not a Python import or resolver error. Ask the environment administrator or Chalk support to provision it when needed. On Chalk-managed serverless environments, customer credentials cannot provision the engine: the API can reject creation with `serverless infrastructure can only be accessed by its owning team`. Do not bypass that restriction or silently switch environments.

`run_workflow` with a local worker does not require a feature deployment or an offline store. Do not create resolver deployments, offline-query datasets, or scheduled queries just to run a Python workflow.

## Define tasks and workflows

```python
# order_workflows.py
import asyncio
from chalk.client import ChalkClient
from chalk.workflows import task, workflow

@task(retries=2, timeout="1m")
def summarize_orders(amounts: list[int]) -> dict:
    return {"orders": len(amounts), "revenue_cents": sum(amounts)}

@workflow
async def daily_order_summary(amounts: list[int]) -> dict:
    return await summarize_orders(amounts)

@workflow
async def regional_order_summary(regions: dict[str, list[int]]) -> dict:
    names = list(regions)
    handles = [summarize_orders.submit(regions[name]) for name in names]
    summaries = await asyncio.gather(*handles)
    return dict(zip(names, summaries))

if __name__ == "__main__":
    client = ChalkClient()
    result = client.run_workflow(
        daily_order_summary,
        {"amounts": [2500, 4800, 1200]},
    )
    print(result)  # {'orders': 3, 'revenue_cents': 8500}
```

- Workflows must be `async def` and deterministic. Keep network calls, filesystem access, randomness, and clock reads inside tasks.
- Tasks may be synchronous or asynchronous. Await a task for sequential execution; use `.submit()` and await the handles for parallel steps.
- Use JSON-serializable inputs and return values. Convert UUIDs to strings when serializing them yourself.
- Retries permit repeated execution. Make external writes safe to repeat, using stable application IDs for idempotency.
- Keep client construction, deployment, and submission inside the `__main__` guard. Worker discovery imports the module. `chalk apply` also rejects module-level Chalk client construction.

## Choose where the worker runs

### In-process verification

```python
result = asyncio.run(daily_order_summary([2500, 4800, 1200]))
assert result == {"orders": 3, "revenue_cents": 8500}
```

This runs tasks locally without a remote orchestrator, remote history, or task images. Use it to verify logic, and label it as local verification. It is not evidence of successful remote execution.

### Durable run with a local worker

`ChalkClient().run_workflow(definition, inputs, workflow_id=...)` starts a remote run and executes its tasks in the calling process. It blocks until the workflow returns; keep the process running. The history remains in the environment after the worker stops.

Use descriptive workflow IDs for runs the user wants to inspect. Re-running with the same ID may be subject to Temporal's duplicate/reuse policy; choose a fresh ID for a new run instead of assuming it replaces an earlier run.

### Independently deployed worker

Read [the current compute workflows docs](https://docs.chalk.ai/docs/compute/workflows) for the deployed-worker path. Inspect installed versions and signatures before calling SDK helpers: `chalkcompute` and `chalkpy` release separately. In particular, do not assume `chalkcompute.deploy_workflow_worker` exists merely because it appears in the docs.

Where supported, deploy a named worker with `chalkcompute.deploy_workflow_worker`, keep its printed task queue, and submit with:

```python
run = ChalkClient().trigger_workflow(
    "daily_order_summary",
    {"amounts": [2500, 4800, 1200]},
    task_queue="the-queue-returned-by-deployment",
    wait=False,
)
print(run.workflow_id, run.run_id)
```

Deployment registers workers; it does not submit runs. A run submitted to a queue without a poller can remain running without executing a task. Keep task-specific imports inside task bodies when tasks use separate images, since every worker imports the source module. Direct and local-worker execution ignore task images.

## Inspect and verify

Inspect workflow type, workflow ID, run ID, task queue, status, task attempts, history, and results in the environment's Python workflows view. The separate `/workflow-orchestrator` view also exposes these executions. Access follows the workflow API's permissions; an authorization failure is not an empty workflow list.

`chalk workflows list/get/get-graph` refers to data-plane offline job workflows, not Python `@workflow` executions. `chalk argo` refers to image builds. Do not use either as proof that a Python workflow ran.

An accepted handle is not completion. Wait for a terminal status and validate the returned result; when troubleshooting a stalled run, check whether a worker polls its exact queue before resubmitting. If provisioning or credentials block remote execution, report that exact blocker and retain the runnable code. Leave workers and execution history available when the user requests it; do not automatically clean up their resources.
