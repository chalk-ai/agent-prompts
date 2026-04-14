---
name: chalk-streaming
description: Use when writing, debugging, testing, or tuning Chalk stream resolvers — Kafka, Kinesis, or PubSub sources, make_stream_resolver patterns, parse expressions, deduplication, late arrival, feature time, resource groups, or streaming worker configuration.
---

# Chalk Streaming

## When to Use

- Writing a new `make_stream_resolver` (Kafka, Kinesis, PubSub)
- Choosing or debugging a parse expression (JSON, Avro, protobuf, header filtering)
- Testing stream parsing with `test_streaming_resolver` / `check_stream_parsing`
- Configuring deduplication, late arrival, or feature time
- Tuning throughput (batch size, poll records, resource groups)
- Understanding how stream workers communicate with engine-grpc
- Setting up materialized windowed aggregations driven by streaming data

## Minimal Example

```python
from pydantic import BaseModel
from chalk.features import features, Primary
from chalk.features.resolver import make_stream_resolver
from chalk.streams import KafkaSource
from chalk.features import _

kafka_source = KafkaSource(name="my_topic")

class OrderMessage(BaseModel):
    order_id: str
    amount: float

@features
class Order:
    id: Primary[str]
    amount: float

get_orders = make_stream_resolver(
    source=kafka_source,
    message_type=OrderMessage,       # JSON body deserialized automatically
    output_features={
        Order.id: _.order_id,
        Order.amount: _.amount,
    },
)
```

## When NOT to Use

- Request-response patterns — use `@online` resolvers instead
- One-off batch loads — use `@offline` resolvers or `chalk upload`
- Topics with no defined schema — define a Pydantic/proto/Avro model first
- Backfilling historical data from a stream — use offline resolvers + timestamp replay

---

## Architecture Overview

**Supported connectors:** Kafka, Kinesis (standard + Enhanced Fan-Out), Google Cloud Pub/Sub

**Components:**

- **Streaming worker** (Rust): polls the message source, batches and parses messages, and forwards results to the execution engine via gRPC
- **Execution engine** (engine-grpc): receives Arrow-serialized batches, runs the resolver function, updates materialized aggregates, and uploads features to the online store
- **Control plane**: provides pause/resume/seek/reset operations for stream workers

**Data flow:**
```
Source (Kafka/Kinesis/PubSub)
  → Streaming worker polls messages
  → Messages batched and parse expression applied
  → Parsed Arrow batch forwarded to engine-grpc
  → Engine runs resolver, returns features
  → Features published to online store + offline store via result bus
```

---

## Writing to the Online and Offline Stores

Stream resolvers write to **both** stores automatically — no special configuration is needed:

- **Online store**: low-latency key-value writes, available immediately for feature serving
- **Offline store**: append-based writes for training data, backfill, and observability; type coercions are applied automatically per warehouse (Snowflake, BigQuery, Redshift)

Use `skip_online` or `skip_offline` to suppress writes to one of the stores:

```python
make_stream_resolver(
    source=kafka_source,
    message_type=MyMessage,
    skip_online=True,   # Write to offline store only (e.g. for training-only features)
    skip_offline=True,  # Write to online store only (e.g. for low-latency serving only)
    output_features={...},
)
```

---

## Materialized Aggregations

When a stream resolver writes to a `DataFrame` relationship, Chalk automatically detects which `windowed(...)` aggregations are affected and updates the relevant time buckets — no special annotation on the resolver is needed. At query time, buckets are merged to produce the final value across the requested window.

**Simple example** — stream resolver drives `sum` and `count` aggregations:

```python
from chalk.features import features, DataFrame, Windowed, windowed, has_many, Primary
from chalk.features.resolver import make_stream_resolver
from chalk.features import _
import chalk.functions as F

@features
class Transaction:
    id: Primary[str]
    amount: float
    ts: datetime
    org_id: str

@features
class Organization:
    id: Primary[str]
    transactions: DataFrame[Transaction] = has_many(
        lambda: Organization.id == Transaction.org_id
    )
    total_spend: Windowed[float] = windowed(
        "1d", "7d", "30d",
        expression=_.transactions[_.amount].sum(),
        materialization=True,   # Chalk updates buckets as stream messages arrive
        default=0.0,
    )
    transaction_count: Windowed[int] = windowed(
        "1d", "7d", "30d",
        expression=_.transactions[_.id].count(),
        materialization=True,
        default=0,
    )

# No special config needed — aggregations update automatically
transaction_stream = make_stream_resolver(
    source=kafka_source,
    message_type=TransactionMessage,
    output_features={
        Transaction.id: _.transaction_id,
        Transaction.amount: _.amount,
        Transaction.ts: F.from_unix_milliseconds(_.timestamp_ms),
        Transaction.org_id: _.org_id,
    },
)
```

### Supported Aggregation Functions

| Function | Description |
|---|---|
| `.sum()` | Sum of values |
| `.count()` | Count of rows |
| `.min()` / `.max()` | Min / max value |
| `.mean()` | Mean (stores sum + count internally) |
| `.std()` / `.var()` | Standard deviation / variance |
| `.approx_count_distinct()` | HLL-based distinct count (Apache DataSketches) |
| `.approx_percentile(quantile=0.5)` | Approximate percentile |
| `.approx_top_k(k=n)` | Top-K by frequency |
| `.min_by_n(field, n)` / `.max_by_n(field, n)` | Top-N rows by field value |

### Custom Bucket Configuration

Buckets align to Unix epoch by default. Use `MaterializationWindowConfig` to customize:

```python
import datetime as dt
from chalk import MaterializationWindowConfig

# Align daily buckets to IST (UTC+5:30) instead of UTC midnight
ist_tz = dt.timezone(dt.timedelta(hours=5, minutes=30))

DAILY_IST = MaterializationWindowConfig(
    bucket_duration="24h",
    bucket_start=dt.datetime(1970, 1, 1, 0, tzinfo=ist_tz),
)

@features
class UserActivity:
    id: Primary[str]
    events: DataFrame[Event] = has_many(...)
    daily_event_count: Windowed[int] = windowed(
        "1d", "7d",
        expression=_.events[_.id].count(),
        materialization=DAILY_IST,
        default=0,
    )
```

### Opting Out

To prevent a stream resolver from updating materialized aggregations it would otherwise affect:

```python
make_stream_resolver(
    ...,
    updates_materialized_aggregations=False,
)
```

### Storage Efficiency

Chalk stores only the minimal aggregate state per bucket — `sum`/`min`/`max` uses ~9 bytes (one float64), `mean` uses ~18 bytes (sum + count). Approximate aggregations (sketches) use 50–200 bytes per bucket.

---

## Deduplication and Late Arrival Handling

### Deduplication

```python
from chalk.features.resolver import make_stream_resolver
from chalk.streams import Deduplication

my_resolver = make_stream_resolver(
    source=kafka_source,
    ...,
    deduplication=Deduplication(on=_.event_id, within="24h"),
)
```

- `on`: field that uniquely identifies a message (e.g., event ID)
- `within`: rolling dedup window (e.g., `"24h"`, `"1h"`)
- Duplicate messages produce `STREAMING_MESSAGE_STATUS_DUPLICATE_SKIPPED` and are not written to any store
- Dedup state is stored in the KV store, keyed by the `on` field value

### Late Arrival

Configure `late_arrival_deadline` on the stream source:

```python
kafka_source = KafkaSource(
    ...,
    late_arrival_deadline="1h",   # Drop messages older than 1 hour
)
```

- Messages where `message.timestamp < now - late_arrival_deadline` are dropped before parsing
- A warning is logged per dropped message (visible in deployment logs)

---

## Testing Stream Resolvers

### `test_streaming_resolver` / `check_stream_parsing`

```python
import fastavro, io

def serialize_avro(schema_dict, record):
    schema = fastavro.parse_schema(schema_dict)
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, schema, record)
    return buf.getvalue()

MATCHING_HEADER = [("Firehose-Event-Type", b"com.example.MyEventV1")]
WRONG_HEADER    = [("Firehose-Event-Type", b"com.example.OtherEvent")]

records = [
    {"profileId": 42, "timestamp": 1700000000000},  # should match
    {"profileId": 99, "timestamp": 1700000001000},  # should be filtered (wrong header)
]

result = chalk_client.test_streaming_resolver(
    resolver=my_resolver.name,
    message_bodies=[serialize_avro(AVRO_SCHEMA, r) for r in records],
    message_headers=[MATCHING_HEADER, WRONG_HEADER],
)

# result.features is a Polars DataFrame — one row per input message
df = result.features

# Matching message: features populated
assert df[0]["my_entity.id"].item() == 42

# Non-matching message: entire row is null (filtered by parse expression)
assert df[1]["my_entity.id"].item() is None
```

**Key details:**
- `message_bodies`: list of raw serialized bytes (Avro, JSON, protobuf), one per message
- `message_headers`: list of `[(name_str, value_bytes)]` — header values must be `bytes`, not `str`
- `result.features`: Polars DataFrame — one row per input message
  - A row of nulls = the message was skipped/filtered by the parse expression
  - Use `.item()` to extract a scalar from a single-element Polars Series

### Batching DF Calls (stream → engine-gRPC)

All messages in a poll cycle are batched into a single Arrow table and forwarded in one gRPC call. Control knobs:

- `STREAMING_CONSUMER_MAX_RECORDS` (default 250): max messages fetched per poll cycle → controls Arrow table row count
- `MESSAGE_PROCESSOR_BATCH_SIZE`: sub-batch messages within a cycle before sending (useful to reduce per-call latency at the cost of more gRPC calls)

---

## Native vs Non-Native Stream Resolvers

**Always use native stream resolvers.** Any resolver created with `make_stream_resolver()` is native by default.

| | Native | Non-Native |
|---|---|---|
| How defined | `make_stream_resolver()` with `_` expressions or Pydantic | Legacy Python |
| Processed by | Rust streaming worker (high performance) | Python worker (deprecated) |
| Recommended | Yes | No — do not use for new resolvers |

---

## Feature Time

The feature time (`observed_at`) for stream-resolved features is set as follows:

1. **Standard resolvers**: feature time = message envelope timestamp
   - Kafka: `message.timestamp`
   - Kinesis: `approximate_arrival_timestamp`
   - PubSub: `publish_time`
   - If the parse function emits an explicit timestamp field, that takes precedence
2. **Windowed resolvers**: feature time = window end time
3. **Fallback**: if no timestamp is available, feature time = processing time (`now`)

To map a message field to feature time in `output_features`, assign it to your namespace's `FeatureTime` feature:

```python
output_features={
    MyEntity.ts: F.from_unix_milliseconds(this.timestamp),  # becomes observed_at
    ...
}
```

---

## Tuning Stream Message Processing

### Key Environment Variables

| Variable | Default | Effect |
|---|---|---|
| `STREAMING_CONSUMER_MAX_RECORDS` | 250 | Max messages fetched per poll cycle |
| `STREAMING_CONSUMER_TIMEOUT_MS` | 10000 | Max wait time per poll batch (ms) |
| `MESSAGE_PROCESSOR_BATCH_SIZE` | (all) | Sub-batch size per engine-grpc call |
| `SINGLEPROCESS_MESSAGES_FOR_RESOLVERS` | (none) | Comma-separated resolver FQNs; process one message at a time (for stateful/side-effectful resolvers) |
| `STREAMING_CONNECTION_TIMEOUT_MS` | 30000 | Initial connector connection timeout (ms) |
| `STREAMING_POLL_BUDGET_MS` | 5000 | Poll retry budget for Kinesis EFO (ms) |
| `CHALK_RESUBSCRIBE_EMPTY_SHARDS_AFTER_DURATION_MS` | (none) | Kinesis EFO resubscribe threshold (ms) |

### Load Balancing

**Kafka:** partitions are distributed across stream worker replicas via the consumer group. Set `group_id_prefix` on `KafkaSource` to namespace your consumer group. Scale replicas up to the partition count for maximum parallelism.

**Kinesis:** shards are distributed across replicas automatically. For Enhanced Fan-Out, each replica subscribes to shards independently via `enhanced_fanout_consumer_name`.

### Resource Group Isolation

Resource groups let you assign specific stream resolvers to **dedicated streaming infrastructure**, isolating workloads by throughput tier, latency requirements, or resource profile.

**How it works:**
- Each streaming server is deployed with a `CHALK_RESOURCE_GROUP` env var
- At startup, the server filters its resolver list to only those matching its resource group
- Resolvers without a `resource_group` run exclusively on the **Default** resource group
- Resolvers with an explicit `resource_group` run exclusively on the matching server — they do not run on Default

**Assigning a resolver to a resource group:**

```python
high_volume_resolver = make_stream_resolver(
    name="high_volume_resolver",
    source=high_volume_source,
    resource_group="high-throughput",   # Only runs on "high-throughput" streaming server
    output_features={...},
)

low_volume_resolver = make_stream_resolver(
    name="low_volume_resolver",
    source=low_volume_source,
    # No resource_group → runs on Default streaming server
    output_features={...},
)
```

**Setup in the Chalk dashboard:**
1. Navigate to **Infrastructure → Resource Configuration → Resource Groups**
2. Click **Add Resource Group** and name it (e.g., `high-throughput`)
3. Add a **Streaming Server** with the desired CPU/memory/scaling config
4. Add a **gRPC Query Server** so the streaming server can route features to the engine
5. Click **Save and Apply Service**, then run `chalk apply` to deploy resolver assignments

**Use cases:**
- Isolate a high-throughput topic from lighter workloads to prevent resource contention
- Run memory-intensive windowed resolvers on nodes with larger memory profiles
- Scale a single high-priority resolver independently of the rest of the pipeline

Resource groups are also used for per-group Datadog metric tagging (tagged as `resource_group`), making it easy to track lag and throughput separately per group.

---

## Common Mistakes

| Mistake | Fix |
|---|---|
| Forgetting `include_message_envelope=True` | Required to access `this.message_headers` and `this.message_data`; without it both fields are `None` |
| Using `_` instead of `this` in envelope-aware parse expressions | `_` refers to the root message body; `this` refers to the full envelope — use `this` when `include_message_envelope=True` |
| Header value passed as `str` instead of `bytes` | `F.map_get(this.message_headers, "X-Type") == b"value"` — the value side must be `bytes` |
| Missing `F.recover()` around `F.avro_deserialize()` | Without `recover`, a single malformed message crashes the entire batch; wrap with `F.recover(expr, None)` |
| Deduplication dropping unexpected messages | Dedup key collision across different entities; use a more unique `on=` field (e.g., include entity ID) |
| Timestamp not reflected in offline store | Feature time defaults to message envelope timestamp; to use a payload timestamp, map it to a `FeatureTime` feature in `output_features` |
| Resolver not running on the expected streaming server | Worker filters by `CHALK_RESOURCE_GROUP` env var — the `resource_group` on the resolver must match exactly (case-sensitive) |

---

## Parsing and Header Filtering

### JSON messages (no parse function needed)

If your topic carries JSON messages that map directly to a Pydantic model, no `parse` expression is required — the streaming worker deserializes each message body automatically:

```python
from pydantic import BaseModel
from chalk.features import _
import chalk.functions as F
from chalk.features.resolver import make_stream_resolver

class TransactionMessage(BaseModel):
    tid: str
    amount: float
    ts_micros: int

get_transactions = make_stream_resolver(
    source=kafka_source,
    message_type=TransactionMessage,   # JSON body deserialized automatically
    output_features={
        Transaction.id: _.tid,
        Transaction.amount: _.amount,
        Transaction.ts: F.from_unix_seconds(_.ts_micros / 1_000_000.0),
    },
)
```

### Avro messages with header filtering

For multi-event topics where messages are distinguished by a header:

```python
from chalk.features import _ as this
import chalk.functions as F
from chalk.features.resolver import make_stream_resolver
from pydantic import BaseModel

class MyEventModel(BaseModel):
    profileId: int
    timestamp: int

EVENT_TYPE = b"com.example.MyEventV1"

parsed_message = F.if_then_else(
    F.map_get(this.message_headers, "Firehose-Event-Type") == EVENT_TYPE,
    if_true=F.recover(
        F.avro_deserialize(this.message_data, schema=AVRO_SCHEMA, target_type=MyEventModel),
        None,
    ),
    if_false=None,
)

my_resolver = make_stream_resolver(
    source=kafka_source,
    include_message_envelope=True,  # Required to access message_headers and message_data
    parse=parsed_message,
    message_type=MyEventModel,
    output_features={
        MyEntity.id: this.profileId,
        MyEntity.event_time: F.from_unix_milliseconds(this.timestamp),
    },
)
```

### Protobuf messages

Use `F.proto_deserialize` in the `parse` expression. If your topic uses the Confluent Schema Registry wire format, strip the 6-byte header with `F.substr` first:

```python
import chalk.functions as F
from chalk.features import _
from chalk.features.resolver import make_stream_resolver
import my_package.events_pb2 as events_pb2

CONFLUENT_HEADER_SIZE = 6

# Strip Confluent header bytes, then deserialize
parsed = F.proto_deserialize(
    F.substr(_, CONFLUENT_HEADER_SIZE, None),
    events_pb2.MyEvent,
)

# Optionally filter on a proto enum field
parsed = F.if_then_else(
    parsed.action == events_pb2.MyEvent.Action.ACTION_CANCEL,
    None,       # skip cancellation events
    parsed,
)

my_resolver = make_stream_resolver(
    source=kafka_source,
    message_type=events_pb2.MyEvent,
    parse=parsed,
    output_features={
        MyEntity.id: _.event_id,
        MyEntity.action: F.proto_enum_value_to_name(_.action, events_pb2.MyEvent.Action),
        MyEntity.event_time: F.proto_timestamp_to_datetime(_.occurred_at),
    },
)
```

Useful proto helper functions:
- `F.proto_deserialize(bytes_expr, ProtoClass)` — deserialize raw proto bytes
- `F.proto_enum_value_to_name(field, EnumClass)` — convert integer enum to string name
- `F.proto_timestamp_to_datetime(field)` — convert `google.protobuf.Timestamp` to datetime

### Key parsing notes
- `include_message_envelope=True` is required to access `this.message_headers` and `this.message_data`
- `F.map_get(this.message_headers, "Header-Name")` for header lookup
- `F.recover(expr, None)` swallows parse errors and returns `None` instead of failing
- `parse` returning `None` → message is silently skipped, no features are written to any store

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `this.message_headers` is `None` at runtime | `include_message_envelope=True` not set on `make_stream_resolver` | Add `include_message_envelope=True` |
| All rows null in `test_streaming_resolver` result | Header value compared as `str` instead of `bytes` | Use `b"value"` (bytes literal) in the parse expression comparison |
| Features not appearing in online store | `skip_online=True` set unintentionally, or resolver assigned to wrong resource group | Verify resolver config and resource group name matches `CHALK_RESOURCE_GROUP` |
| Features not appearing in offline store | `skip_offline=True` set, or feature time is far in the future/past | Check `skip_offline` flag and timestamp field mapping |
| Dedup silently dropping valid messages | `on=` field value collides across different entities | Use a globally unique dedup key (e.g., `f"{entity_id}:{event_id}"`) |
| `avro_deserialize` crashes on one bad message, dropping the whole batch | Missing `F.recover()` wrapper | Wrap with `F.recover(F.avro_deserialize(...), None)` |
| Resolver not polling from Kafka topic | Wrong `group_id_prefix` or topic name mismatch | Check `KafkaSource` config and consumer group in Kafka UI |
