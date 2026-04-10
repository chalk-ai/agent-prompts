---
name: writing-static-chalkdf
description: Use when writing Chalk @online(static=True) resolvers with chalkdf — exploding has-many relations, accessing Now, self-joins, transitive closure, windowed filters, or debugging chalk apply serialization errors like "unable to deserialize Chalk DataFrame" or "LazyFramePlaceholder: unsupported type".
---

# Writing Static ChalkDF Resolvers

## Overview

`@online(static=True)` resolvers **run once at plan time** (`chalk apply`), not at query time. The function executes with a real `DataFrame` backed by a `LazyFramePlaceholder`, building up a lazy DAG. That DAG is then serialized to proto and stored — at query time Chalk executes the pre-built plan against real data.

**What this means:**
- **Arbitrary Python is allowed** in the function body — loops, helper functions, constants, conditionals based on Python values all work. They run at plan time and just build up more plan nodes.
- **The return value must be a lazy `DataFrame`** (an unexecuted plan). Never call `.run()`, `.to_pandas()`, or anything that materializes data mid-chain — these try to execute against placeholder data at plan time and fail.
- **Only serializable chalkdf nodes can be in the plan.** Some operations produce proto nodes the serializer doesn't support, causing a cryptic error at `chalk apply` time (not at runtime).

This is why Python loops like `for _ in range(5): groups = pointer_jump(groups)` work fine — they run at plan time and produce a larger (but valid) plan graph.

## Resolver Skeleton

```python
from chalk import online
from chalk.features import DataFrame, Now, _
import chalk.functions as F
from datetime import datetime
from src.models import Parent, Child

@online(static=True)
def my_resolver(
    df: DataFrame[
        Parent.id,
        Parent.some_field,
        Now,                          # include if you need current time
        Parent.children[              # has-many: declare subset of fields
            Child.id,
            Child.created_at,
        ],
    ],
) -> DataFrame[Parent.id, Parent.output_field]:
    return _impl(df)   # helper delegation is allowed
```

## Column Naming

### Feature class → column name conversion

Chalk converts feature class names to `snake_case` dotted paths:

```
ClassName.field_name  →  "class_name.field_name"
```

Examples:
```python
str(Parent.id)          # → "parent.id"
str(Child.created_at)   # → "child.created_at"
str(MyThing.amount)     # → "my_thing.amount"
str(Parent.children)    # → "parent.children"   (the list column itself)
```

Rule: **CamelCase class name → snake_case prefix**, then `.field_name`.

### Has-many struct field names

After `explode()`, the list column becomes a struct. Fields inside the struct use the **full dotted feature name** — you access them via `getattr` chains (see below). When building test data, struct field keys are bare names (no prefix):

```python
pa.struct([("id", pa.string()), ("created_at", pa.timestamp("us")), ...])
```

### In resolver code

```python
# Temp/intermediate columns: plain strings
.with_columns({"slot": ..., "rn": ...})

# Output features: alias to full feature string
.select(_.computed.alias("parent.output_field"))
# Or: .select(_.computed.alias(str(Parent.output_field)))
```

### `Now` column name

`Now` in `DataFrame[..., Now]` injects column `"__chalk__.now"`. Access via `df.col()`, not `_`:

```python
df.col("__chalk__.now")    # correct
# _.chalk_now              # WRONG
```

## Accessing Has-Many Relations

**Pattern:** save a skeleton before exploding, explode, work on flat df, join skeleton back.

```python
# 1. Save skeleton (key cols + now, before exploding changes the row cardinality)
df_skeleton = df.with_columns({
    "parent.id": _.parent.id,
    "__chalk__.now": df.col("__chalk__.now"),
})

# 2. Explode the has-many column
df_exploded = df.explode(str(Parent.children))

# 3. Extract nested struct fields via getattr chains
df_flat = df_exploded.with_columns({
    "child.created_at": getattr(getattr(_, str(Parent.children)), str(Child.created_at)),
    "child.created_date": getattr(getattr(_, str(Parent.children)), str(Child.created_date)),
})

# 4. Compute on df_flat ...

# 5. Join results back to skeleton (one row per parent)
final = df_skeleton.join(result, on=["parent.id", "__chalk__.now"], how="left")
```

## Safe vs Unsafe Operations

### Safe
- `filter()`, `join()`, `with_columns()`, `select()`, `drop()`, `rename()`, `fill_null()`, `explode()`
- `order_by()`, `with_unique_id()`, `distinct_on()`, `union_all()`
- `group_by().agg()` / `.agg([keys], expr)` with: `count()`, `max()`, `min()`, `sum()`, `mean()`
- `F.unix_seconds()`, `F.from_unix_seconds()`, `F.cast()`, `F.date_trunc(col, unit)`
- `F.coalesce()`, `F.if_then_else()`, `F.is_not_null()`, `F.is_null()`
- Arithmetic `+`, `-`, `*`, `/`; Boolean `&`, `|`, `~`; Comparisons `==`, `!=`, `>`, `<`, `>=`, `<=`
- `_.col.regexp_like(pattern)`, `_.col.lower()`, `_.list_col.contains(value)`
- Helper functions: `return _impl(df)` delegation is fine

### Unsafe → Alternatives

| Broken | Use instead |
|--------|-------------|
| `_.col.is_in([list])` | `(_.col == "a") \| (_.col == "b") \| ...` |
| `F.total_seconds(timedelta)` | `F.unix_seconds(t1) - F.unix_seconds(t2)` |
| `F.year(col)`, `F.month(col)` | Integer slot: `F.unix_seconds(F.cast(col, datetime)) / MONTH_SECS` |
| `_.col.stddev()` | Not serializable — remove or approximate |
| `WindowExpr` / `.shift()` | Self-join + group-by aggregation patterns |
| `from_pandas()` / `MaterializedDataFrame` | Pure `_` DSL only |
| `lit("str", pa.large_utf8())` | Use separate `@online` resolver for string constants |
| F-function wrapping `agg` expression | Aggregate first (plain `.max()`), then `with_columns(F.unix_seconds(...))` |

## Common Patterns

### Windowed filter using `Now`

```python
THIRTY_DAYS_IN_SECONDS = 30 * 24 * 3600

df_30d = df_flat.filter(
    (df_flat.col("child.created_at") >= F.from_unix_seconds(
        F.unix_seconds(F.date_trunc(df_flat.col("__chalk__.now"), "day"))
        - THIRTY_DAYS_IN_SECONDS
    ))
    & (df_flat.col("child.created_at") < df_flat.col("__chalk__.now"))
)
```

### Double aggregation (e.g. max daily count)

```python
result = (
    df_30d
    .agg(                                      # first: count per day per parent
        ["child.created_date", "parent.id", "__chalk__.now"],
        _.count().alias("daily_count"),
    )
    .agg(                                      # then: max across days
        ["parent.id", "__chalk__.now"],
        _.daily_count.max().alias("parent.max_daily_count"),
    )
)
```

### Deduplication (keep earliest-created, smallest-id)

```python
min_created = items.agg(
    ["parent.id", "start", "end"],
    _.created_at.min().alias("min_created_at"),
)
items = (items
    .join(min_created, on=["parent.id", "start", "end"], how="inner")
    .filter(_.created_at == _.min_created_at))

min_id = items.agg(
    ["parent.id", "start", "end", "created_at"],
    _.item_id.min().alias("min_item_id"),
)
deduped = (items
    .join(min_id, on=["parent.id", "start", "end", "created_at"], how="inner")
    .filter(_.item_id == _.min_item_id))
```

### Row numbering + self-join running aggregation

```python
ordered = deduped.order_by("start", "end").with_unique_id("rn")

prev_max = (
    ordered
    .join(ordered, on=["parent.id"], how="left", right_suffix="_prev")
    .filter(_.rn_prev < _.rn)
    .agg(["parent.id", "rn"], _.end_prev.max().alias("prev_max_end"))
)
with_max = ordered.join(prev_max, on=["parent.id", "rn"], how="left")
```

### Transitive closure via pointer jumping (O(log n) iterations)

```python
def pointer_jump(groups):
    lookup = (groups
        .select("parent.id", "rn", "group_id")
        .rename({"rn": "group_id", "group_id": "parent_group_id"}))
    return (groups
        .join(lookup, on=["parent.id", "group_id"], how="left")
        .with_columns({
            "group_id": F.if_then_else(
                F.is_not_null(_.parent_group_id) & (_.parent_group_id < _.group_id),
                _.parent_group_id,
                _.group_id,
            )
        })
        .select("parent.id", "rn", "group_id", ...))

for _ in range(5):   # 5 iterations → handles chains up to 32 hops
    groups = pointer_jump(groups)
```

### Equality chains (replaces `is_in`)

```python
# Instead of: _.category.is_in(["a", "b", "c"])
is_match = (_.category == "a") | (_.category == "b") | (_.category == "c")
```

### F-functions after agg, not inside

```python
# WRONG — F inside agg fails serialization:
.agg(["parent.id"], F.unix_seconds(F.cast(_.date.max(), datetime)).alias("secs"))

# CORRECT — aggregate plain, then apply F:
.agg(["parent.id"], _.date.max().alias("max_date"))
.with_columns(F.unix_seconds(F.cast(_.max_date, datetime)).alias("secs"))
```

## Local Testing

Static resolvers are fully testable locally — no Chalk server needed for logic tests. Only serialization requires `chalk lint`.

### Setup

```bash
uv pip install chalkdf
```

### Test logic by calling `_impl` directly (manual)

Extract resolver logic into a `_impl(df)` helper, build a synthetic `DataFrame`, call it, assert on the result:

```python
import pyarrow as pa
from chalkdf import DataFrame
from datetime import datetime, date

child_type = pa.struct([
    ("id", pa.string()),
    ("created_at", pa.timestamp("us")),
    ("created_date", pa.date32()),
])

table = pa.table({
    "parent.id": ["p1", "p2"],
    "__chalk__.now": pa.array(
        [datetime(2024, 2, 1), datetime(2024, 2, 1)],
        type=pa.timestamp("us"),
    ),
    "parent.children": pa.array(
        [
            [
                {"id": "c1", "created_at": datetime(2024, 1, 10), "created_date": date(2024, 1, 10)},
                {"id": "c2", "created_at": datetime(2024, 1, 10), "created_date": date(2024, 1, 10)},
            ],
            [
                {"id": "c3", "created_at": datetime(2024, 1, 20), "created_date": date(2024, 1, 20)},
            ],
        ],
        type=pa.list_(child_type),
    ),
})

df = DataFrame.from_arrow(table)
result = _impl(df).to_pandas()
assert result.loc[result["parent.id"] == "p1", "parent.max_daily_count"].iloc[0] == 2
print(result)
```

### Building has-many test data

Has-many columns are `pa.list_(pa.struct([...]))`. Struct field keys are **bare names** (no feature prefix):

```python
item_type = pa.struct([
    ("id", pa.string()),
    ("start", pa.date32()),
    ("end", pa.date32()),
    ("created_at", pa.timestamp("us")),
])

table = pa.table({
    "parent.id": ["p1"],
    "parent.items": pa.array(
        [[
            {"id": "i1", "start": date(2024, 1, 1), "end": date(2024, 1, 3), "created_at": datetime(2024, 1, 1)},
            {"id": "i2", "start": date(2024, 1, 3), "end": date(2024, 1, 5), "created_at": datetime(2024, 1, 2)},
        ]],
        type=pa.list_(item_type),
    ),
})
```

### Test serialization

```bash
chalk lint        # traces all resolvers and checks proto serialization
```

Logic errors surface in `.to_pandas()`. Serialization errors only appear at `chalk lint` / `chalk apply` time.

## Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| `unable to deserialize Chalk DataFrame from encoded plan` | F-function inside `agg()` | Move F-calls to `with_columns()` after aggregation |
| `LazyFramePlaceholder: unsupported type <X>` | Unsupported node (stddev, WindowExpr, etc.) | See Unsafe table |
| `AttributeError: 'MaterializedDataFrame'` | `is_in([list])` or `.run()` mid-chain | Use equality chains; never materialize |
| `ValueError: The parent for cast should be an underscore` | `lit("str", pa.large_utf8())` | Use separate `@online` resolver for string mapping |
