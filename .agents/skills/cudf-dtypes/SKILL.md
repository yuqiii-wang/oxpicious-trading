---
name: "cudf-dtypes"
description: "cudf.pandas COMPUTE-LAYOUT preference + dtype rules for this repo. Use BEFORE writing or refactoring ANY dataframe / pandas / numpy / cudf code in builds.*/analysis.* — rolling, groupby, shift/diff, gathers, dtype choices, DB fetch/write paths — and whenever a run logs [cudf fallback] lines or is slow on GPU. Covers the GPU-first layout (whole-frame pandas via shared grouped helpers; host numpy only as a per-group boundary core; NEVER mid-flow hybrids), datetime64 units ([ns]/[us], never [s]/[D]), float64 vs Decimal, NaN-not-nullable, proxy-ndarray unwrapping, and the fallback-log verification workflow."
---

# cuDF-safe dtype selection — what dtype in what scenario

Every `[cudf fallback]` line is one pandas op that failed the cuDF fast path,
dropped to CPU pandas, and paid a GPU↔CPU transfer. The 2026-09 fallback log
studies (aggregated over temp_scripts/*.log) showed **the overwhelming
majority were dtype choices, not missing cuDF features**:

| logged signature | count | root cause |
|---|---|---|
| `ndarray.__getitem__: Unsupported dtype datetime64[s]` | 99,000 | `[s]`-unit date columns (opp_pair runs) |
| `Series.__getitem__: MixedTypeError: Cannot have NaN with mixed` | 13,413 | object-dtype / python-date columns (bond build) |
| `IndexOpsMixin.tolist: cuDF does not support … tolist()` | 9,354 | calling `.tolist()` on proxied GPU columns |
| `Index.__contains__: Forcing slow path (transfer blocking)` | 5,254 | `c in df.columns` / `len(df)` per-op metadata dispatch |
| `DataFrame ctor: Unsupported type <class 'numpy.ndarray'>` | 4,836 | object-dtype ndarrays in dict-of-arrays ctor |
| `pandas.isna/notna: NotImplementedError` | ~8,500 | `pd.isna` on (proxy) ndarrays |
| `astype/getitem/isna: Unsupported dtype datetime64[ns]` | 2,400 | fast-path ops on **proxy-subclass** ndarrays |
| `OpsMixin.__add__: Common type of bool with numeric dtypes …` | 240 | bool×numeric arithmetic |
| `to_numpy: cannot convert to float64 … with missing values` | ~630/run | `to_numpy(dtype=float64)` on null-bearing columns |

The bar (already stated in signals-live-vs-history): **zero `[cudf fallback]`
lines outside the sanctioned boundary helpers.** The scenario table below is
how you get there.

## Compute-layout preference (GPU-first) — fixed 2026-10-02

Established by the roof/floor refactor (bit-identical A/B, bands step
440s → 363s, fallback count 2742 → 2714). Pick the layer BEFORE writing
the compute:

| Layer | Form | Verdict |
|---|---|---|
| Whole-frame pandas | grouped rolling / shift / diff via the shared helpers (`grouped_rolling_agg`, `grouped_shift`, `groupby().diff()`), elementwise `where`-chains on frame columns | GPU — the DEFAULT for every new compute; prefer ONE whole-frame grouped pass over per-group loops |
| Sanctioned host core | ONE per-group numpy block for gather/index algorithms — positional gathers, doubling/sparse tables, row×day 2D grids, PostgreSQL array-literal building; unwrap once via `host_array` at entry, materialize via the REAL DataFrame ctor (`pd.DataFrame._fsproxy_slow`) at exit | host — allowed ONLY where no cuDF-native formulation exists; document the boundary in the module docstring |
| Mid-flow hybrid | `pd.Series(host_ndarray, index=...)`, `Series.take(host_positions)`, host-built masks between GPU ops | BANNED — cudf.pandas 26.08 returns garbage-length Series in this flow (probes: temp_scripts/probe_take.py + dbg_len3.py, 2026-10-02); the ctor class banned in the table below |

Hard rules from the same refactor:

- `np.searchsorted(positions, x)` → rank arithmetic on the cumulative
  valid-count prefix: `searchsorted(V, x, 'left') == pref[x]` and
  `searchsorted(V, x, 'right') - 1 == pref[x + 1] - 1` (see
  `ohlc_vector._select_anchor_positions`).
- Scalar-`pd.NaT` setitem infers a **str** column under cudf — init date
  columns with a host `np.full(n, NaT, 'datetime64[ns]')` buffer
  (`ohlc._init_date_columns`).
- Batched writes stay: one REAL concat + one `.loc` write per column
  (B-A3), host arrays in, never proxy objects.
- A dataframe refactor is DONE only when the A/B verification passes:
  identical per-code md5 fold of the output tables
  (temp_scripts/checksum_code.py pattern) AND the `[cudf fallback]`
  count/signature mix does not increase.
- Reference implementations: `analysis/mov_ave_spread/steps/roof_floor_bands.py`
  (whole-frame phases A–C + host event grid D) and
  `analysis/mov_ave_spread/_common/ohlc_vector.py` (host core + rank bounds).

## Scenario → dtype table

| Scenario | Use | Never |
|---|---|---|
| Date/time read from PostgreSQL | `extract(epoch from col)::float8` in SQL → `epoch_col_to_dt64(df[col], index=df.index)` → `datetime64[us]` | python `date`/`datetime` object columns; `datetime64[s]`; `datetime64[D]` in frames |
| Date column meeting a pandas-native frame (wide ops, rolling corr, host kernels) | `epoch_ns_array(vals)` → raw host `datetime64[ns]` ndarray | mixing units across a merge/concat/isin — cuDF falls back on mixed-unit joins |
| Aligning two date columns (concat / merge / merge_asof / isin) | `to_dt64(x, unit=…)` both sides to the SAME unit | one `[us]` side + one `[ns]` side |
| Any numeric measure (price, ratio, score) | `float64` end-to-end (`::float8` at the SQL source) | SQL NUMERIC → asyncpg `Decimal` objects (poison every downstream op); `float32` (no repo convention needs it; pandas-exact semantics validated on float64) |
| Nulls inside a numeric compute column | `NaN` in a `float64` column | `Int64`/`boolean` nullable dtypes inside cudf frames; int columns that can hold NaN (`cannot convert float NaN to integer`) |
| Flag / indicator column riding a frame | plain `int64` 0/1 (`frame.assign(flag=np.int64(0))`) | nullable `boolean` — `<NA>` masks poison the frame into object dtype |
| Boolean masks in compute | PURE `bool` after filling nulls BEFORE comparing: `ta.fillna(0.0) > 0`, `(rb.fillna(-1.0) == 0) & ta_pos` | comparing nullable `<NA>`-yielding series; then storing the mask back into the frame |
| Bool values in arithmetic (`flag * price`, `mask + 1`) | cast first: `mask.astype("float64")` or `astype("int64")` | direct bool×numeric ops — cuDF raises `Common type of bool with numeric dtypes is not supported` |
| Nullable ints at the emit/writer boundary only | `astype("Int64")` (or `to_numpy(dtype="object", na_value=None)`) just before row materialization | `Int64` in the long compute frame (Kleene `<NA>` semantics poison grouped ops) |
| Ids / codes (`code`, `sec_type`, `industry_id`) | string columns as groupby/merge/sort keys only (hash ops are cuDF-native) | numeric arithmetic on them (`NumericalColumn * StringColumn`); `series.unique().tolist()` → use `host_unique`; cupy has no str support — unwrap to host numpy first |
| Closed-vocab labels (`regime`, `side`, `action`, `weighting`, `rank_side`, `pool_size`) | the DB column is SMALLINT (its `stats.label_dim` code, mirrored by `_common.labels` + `data_viz/api/services/label-codes.ts`); frames carry the int16 code end-to-end, TS decodes at the API boundary (`labelDecodeSql`) | label STRINGS inside any cudf frame — they are the cuDF 26.08 string host-transfer fault class (cudaErrorIllegalAddress after sort/merge/concat); log lines decode via the `*_LABEL` maps |
| Frame-only label passengers (`sec_type` attached per loop, `industry_id` in margins) | attach as int16 frame code (`np.int16(SEC_TYPE_CODE[st])` / `_industry_code_frame` factorize), compute on the code, re-materialize the TEXT at the write boundary from the loop constant or the small code frame (`materialize_sec_type_text` in mov_ave_spread steps) | a constant string column riding a big frame through sort/merge/concat then `host_array` transfer (the `rsi.py` `_host_group_bounds` crash site pre-2026-10-05) |
| Python dates for asyncpg DATE / row dicts | `to_py_dates(df, cols)` or `sanitize_for_db_insert(date_cols=…)` at the FINAL step | object-date columns anywhere upstream — one such column makes every later frame op (even unrelated numeric access) pay MixedTypeError |
| Iteration / membership / dtype probes | `safe_columns`, `column_subset`, `host_dtypes`, `host_isna`, `host_unique`, `host_array` (all in `_common.df_utils`) | `c in df.columns`, `len(df)` in loops, per-column `pd.api.types.is_*_dtype`, `pd.isna(ndarray)`, `.tolist()` on proxied values |

## The datetime64 unit rules (the 99,000-fallback class)

cuDF's fast path supports `[ns]` and `[us]` at frame level; **`[s]` and `[D]`
poison frames**, and proxy-ndarray ops (`astype`, `tolist`, `searchsorted`,
`isna`, `__getitem__`) are effectively **`[ns]`-only**.

- `datetime64[us]` — the DB-READ convention (default of `epoch_col_to_dt64`
  and `to_dt64`): python-datetime resolution, range past the 9999-12-31
  sentinel dates that overflow `[ns]` at 2262. Fine inside frames for
  sort/groupby/merge against other `[us]` columns.
- `datetime64[ns]` — the WIDE-OP convention (`epoch_ns_array`): matches
  pandas-native Timestamps so cudf merge/isin/concat against ns frames hits
  the GPU hash path, and the only unit with a proxy fast path.
- `datetime64[s]` — never. Feeding python datetime objects into the frame
  ctor produced `[s]` under cudf.pandas' auto-convert while host pandas
  produced `[ns]` — "a backend-dependent accident" per the
  `epoch_col_to_dt64` study; one `[s]` column = every downstream ndarray
  op falls back. Read dates as epoch `::float8` and convert explicitly.
- `datetime64[D]` — never inside a frame (`__setitem__` ValueError). `[D]`
  exists only as the terminal host-side cast when materializing python
  `date` objects (`to_py_dates`, `sanitize_for_db_insert` date_cols branch).
- Epoch math always on RAW host numpy: seconds float64 →
  `astype("datetime64[s]")` → up/down-cast to the target unit (exact; numpy
  reads the integer as a count of the TARGET unit, so a direct
  `astype("datetime64[us]")` would misread seconds as microseconds).
- `.dt.date` is not implemented by cuDF (per-element fallbacks) — use
  `to_py_dates` or `.dt.floor("D")` on host arrays.

## The proxy-ndarray discipline (the "transfer is blocked" class)

`series.to_numpy()` / `.values` under cudf.pandas return a **proxy-subclass
ndarray**: every downstream numpy op dispatches through the cudf fast/slow
machinery (profiling showed 128 of 136 s per long-history code burned there)
and non-`[ns]` datetime units raise `Unsupported dtype` on it.

- Unwrap ONCE at the pandas→numpy boundary: `host_array(x)` — then
  everything downstream is plain host numpy, any dtype, zero dispatch.
- Never feed proxy arrays into the `pd.DataFrame(dict)` ctor — that is the
  `Unsupported type <class 'numpy.ndarray'>` / ctor-`datetime64[ns]` storm.
- `pd.isna(arr)` has no fast path for ndarrays at all → `host_isna(arr)`
  (branches on `arr.dtype.kind`: `np.isnan` / `np.isnat` / all-False for
  int-bool / `x != x` for object).
- Metadata (`df.columns`, `df.dtypes`) is one explicit transfer via
  `np.asarray` — `safe_columns` / `host_dtypes`; then branch on `.kind`
  host-pure instead of per-column `pd.api.types.*` probes.

## NaN / null semantics in compute

- Keep NaN in float64 columns through all compute; `where()`/masking
  preserves it; cuDF grouped rolling honors NaN exclusion natively.
- Fill BEFORE comparing when you need a pure-boolean mask
  (`fillna(0.0)`, `fillna(-1.0)` sentinels) — comparisons on nullable
  dtypes yield `<NA>` and poison the frame.
- `to_numpy(dtype=np.float64, na_value=np.nan)` — without `na_value` a
  null-bearing column raises per call (~630 fallbacks per stock-scale run).
- For asyncpg bulk writes: ints must stay python ints
  (`to_numpy(dtype="object", na_value=None)` — never float64-coerce; asyncpg
  encodes by value and would send float8 for integer DB columns). All of
  this is already inside `sanitize_for_db_insert` — call it, don't re-roll.
- NaN-containing EMA inputs: `compute_emas` falls back to pandas on purpose
  (the discounted-sum CUDA kernel would propagate NaN) — pre-clean or accept
  the logged fallback.

## Misdiagnosis guard — these are NOT dtype problems

cudf.pandas legitimately lacks some ops; no dtype choice fixes them. Route
per the gpu-df-compute playbook (`_dev_agent/skills/gpu-df-compute/SKILL.md`):
`rolling().corr()/cov()` → `pairwise_rolling_corr` (CuPy kernel); FFT →
`cupyx.scipy.fft`; `ewm(adjust=True)` → pandas path; `merge(indicator=True)`;
`GroupBy.apply` with kwargs. Also expect (sanctioned) fallback lines inside
the boundary helpers themselves (`to_py_dates`, `sanitize_for_db_insert`,
`dates_to_python`, `value_points`, `frame_records`, `csv_copy` rendering).

## Verifying a change

1. `activate()` enables the fallback logger by default (`LOG_FAST_FALLBACK=1`);
   export `CUDF_FALLBACK_TRACE=1` to append `@ file:line` caller locations.
2. Run the task (WSL conda, `--sec-type index` scope first; `etf` if margin).
3. Grep the captured stdout: `grep "\[cudf fallback\]" <log>` — investigate
   every line outside the sanctioned helpers; recurring signatures mean a
   dtype contract breach upstream (find the column:
   `df.dtypes` via `host_dtypes`, look for `object`, `datetime64[s]/[D]`,
   `Int64`, `boolean`).
4. A frame that is clean at construction usually stays clean: enforce dtypes
   at the fetch boundary (`::float8` + `epoch_col_to_dt64`) rather than
   patching ops downstream.

## Re-validated live on cuDF 26.08 (2026-09-27 probes + builds.bond run)

Still falls back (fix these): `pd.to_datetime(errors="coerce")` on a Series
(use the try-clean-parse/except-coerce idiom — see pboc_omo.py, sse
`__main__.py`); `Series.tolist()` on proxied values (use `host_array` first);
`astype(int)` on NaN-bearing floats; `c in df.columns` per check
(`column_subset`); `read_csv(encoding=…)` (cudf lacks the kwarg — the host
fallback on tiny CSVs is the sanctioned small-data route); scalar
`Timestamp.date()` in log lines.

No longer falls back (historical signatures above are version-stale — do
NOT churn working code over them): `to_numpy(dtype=float64)` on NaN-bearing
columns; `pd.to_numeric(errors="coerce")`; `Series.map(dict)`;
`to_dict(orient="records")`; isin/merge/compare across mixed datetime64
units ([ns] vs [s]/[us]) — correct results, zero fallback lines; `[D]`
array setitem + groupby; nullable `Int64` columns in merges (only dtype
printing falls back). The old "mixed-unit ops fall back" rule remains the
alignment convention for NEW code (`to_dt64`) but is not a live error class.

## Study scope for analysis & strategy (MANDATORY)

To do study in analysis and strategy, only check by industries and
indices (no etf and stocks unless explicitly said), only these 10 indices
(000001, 931743, 931160, 931071, 000015, 930708, 931994, 931594, 000683,
000991), and only write the 10 indices to DB table, unless otherwise
explicitly instructed then run all indices.
