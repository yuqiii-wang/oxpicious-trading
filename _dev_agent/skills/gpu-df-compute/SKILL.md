---
name: "gpu-df-compute"
description: "GPU DataFrame compute playbook for this repo: cuDF-supported ops, CuPy fallback for ops cuDF lacks (rolling corr, FFT), CPU final rollback; how to arrange pipeline steps to avoid GPU/CPU transfer churn; NaN/dtype compatibility; partition-key chunking against OOM; key-major bulk COPY writes. Invoke when writing or refactoring builds.*/analyze.* DataFrame pipelines, adding GPU acceleration, or debugging cudf fallback / OOM / slow rolling ops."
---

# GPU DataFrame Compute Playbook

Validated on RTX 5090 32 GB + cuDF 26.08 + CuPy 14.1 + pandas 3.0 (cudf.pandas transparent mode, activated per entry point via `_common.df_utils._activate.activate()` BEFORE first pandas import).

## 1. Backend support matrix — which op runs where

Route every op through this cascade: **cuDF → CuPy → pandas CPU**.

### cuDF-native (stay in cudf.pandas transparent path)
- Rolling: `count / sum / mean / min / max / std / var` (incl. grouped)
- `ewm` (mean/std), `groupby.agg/diff/shift`, hash `merge`/`join`, `merge_asof`
- Element-wise arithmetic, comparisons, `where`/`isin` on numeric dtypes
- Pivot/`pivot_table`, `sort_values`, `notna` on numeric/datetime64 columns
- Boolean matmul (`valid.T @ valid`) — uses pip-installed `nvidia/cublas` wheels; a bare `ctypes.CDLL("libcublas.so.12")` probe FAILS (doesn't search site-packages) — do not conclude cuBLAS is missing from that.

### cuDF does NOT implement → route to CuPy explicitly
- **`rolling().corr()` / `rolling().cov()`** — ANY form (no-arg pairwise AND DataFrame-vs-Series) raises under cudf.pandas and silently falls back to slow pandas CPU (~8 s/M rows). Use `_common.df_utils.pairwise_rolling_corr(wide, window, min_periods=2)` — batched cumsum-algebra kernel, ~24× faster, pandas-exact semantics, validated cell-by-cell.
- **FFT** — not in cuDF scope at all. Route explicitly to CuPy (see "FFT routing" below); `cupyx.scipy.fft` is the SciPy-subset equivalent (DCT/DST, `get_fft_plan()`).
- Anything needing object-dtype intermediates (see §3).

### FFT routing (validated: `analyze/fourier_freqs/compute.py`)
cuDF has no FFT, so the transform is hand-routed; detrend/stride ops (`sliding_window_view`, mean-subtract) stay in the cudf.pandas-chosen array module — ONLY the rfft itself is routed.
- `_cupy_available()` — cached probe: `import cupy` + `cp.cuda.runtime.getDeviceCount()`; returns False on any error (reuse the `rolling_corr` pattern).
- `_rfft(windows, axis=1)` cascade:
  - CuPy present → `cp.asarray(windows) → cp.fft.rfft(axis=axis) → .get()` (cuFFT GPU; returns complex128).
  - else `np.fft.rfft(windows, axis=axis)` (CPU).
  - wrap CuPy branch in `try/except MemoryError` → numpy fallback.
- Per-(code, range_days) windows are small, so no VRAM-cap check needed; `MemoryError` covers concurrent-process VRAM shrink.
- Log the chosen backend once (cupy/numpy) for ops visibility. Downstream `np.abs(fft_result)` works identically on real or complex, so returning complex128 keeps all amplitude/argmax logic unchanged.

### Final rollback: pandas CPU
- Happens automatically in cudf.pandas (prints `[cudf fallback] ...` per call).
- Small data below breakeven: check `_common.df_utils.should_use_gpu(df, op_type=...)` (awareness/logging only — do NOT branch code paths on it; the single pandas code path handles both).
- Breakevens per op_type live in `_common/df_utils/_thresholds.py` (RTX 5090-benchmarked; conservative 4× multiplier).

**Upgrade note:** these are API gaps, not version lag — cuDF 26.08 (latest) still lacks Rolling.corr. No package upgrade fixes them. The only path is algorithmic (CuPy batched kernels).

## 2. Arrange steps to avoid GPU↔CPU transfer churn

Rule: **one wide batched op beats N small ops**. Every cudf.pandas fallback pays a full H2D+D2H round-trip.

- **NEVER loop over pairs/subjects doing small ops.** The original correlations looped ~17,500 × (merge + 4 rolling-corr) — transfer overhead dominated compute ~100×. Restructure to per-pool wide (date × entity) matrices, then ONE tensor op per window.
- **Compute at the widest level, emit at the key level.** Heavy math (corr tensors) runs per POOL (max GPU parallelism); output frames are constructed per partition key (industry_id) for writes. Two-phase: phase 1 = emit masks + base frames (numpy bookkeeping), phase 2 = one batched tensor per window, assign columns by fancy indexing.
- **Single H2D, single D2H per batch.** `_cupy_tensor` transfers the (T, N) matrix once, does all window algebra on-device (cumsum chains), returns the (T, N, N) tensor once.
- **Vectorized bookkeeping replaces Python loops:** pairwise overlap counts via ONE boolean matmul `valid.T @ valid`; emit masks via `valid[:, [ai]] & valid[:, b_idx]`; row extraction via `np.nonzero` + fancy indexing.
- **Serialize GPU jobs.** Two concurrent CUDA processes (e.g. a build + a validation script) each hold VRAM; cuDF's allocator only returns memory to the OS at process exit. Never run validation temp scripts while a build/analyze is running.

## 3. NaN and dtype compatibility

### Object dtype is the #1 GPU poisoner
- cuDF raises `MixedTypeError: Cannot convert a date of object type` / `ValueError: Unsupported dtype object` / `RuntimeError: Fast-to-slow transfer is blocked` on object columns. Each occurrence = a forced CPU fallback + transfer.
- **Keep dates as `datetime64` through ALL compute** (`pd.to_datetime` right after DB fetch). Convert to python `date` objects ONLY in the emitted rows (asyncpg boundary: `wide.index.date`).
- String/id columns (`industry_id`, `pool_size`): keep them OUT of the numeric compute path — build them into output frames from numpy arrays, never through GPU ops.

### NaN semantics (pairwise rolling)
- pandas/cuPy exclusion is PAIRWISE: a date counts for pair (i, j) only when BOTH are non-NaN. The cumsum kernel masks each operand by the JOINT validity `m[t,i,j] = valid[t,i] & valid[t,j]`.
- Partial windows count (truncated head windows match pandas rolling exactly): window sum at t = `c[t+1] - c[max(0, t-W+1)]` over a zero-prepended cumsum.
- `min_periods` applies to joint-valid counts, not window length.

### Degenerate windows → NaN (three guards, all required)
1. **Zero variance** (`d1*d2 <= 0`): pandas emits ±inf (num/0 quirk); emit NaN instead — both become SQL NULL after `sanitize_for_db_insert`.
2. **Exactly-stale windows**: stale composite indices repeat the exact same value for days. True variance is exactly 0, but cumsum rounding leaks ~1e-9 into d1/d2 → corr = noise/noise = arbitrary (observed ±184 in production!). Detect exactly: all consecutive pairs inside the (partial) window valid AND identical (a cumsum over `~(x[t]==x[t-1])`) → NaN.
3. **|corr| > 1 + 1e-9 clamp**: mathematically impossible; any such value is num/den rounding garbage → NaN.

### Precision
- Mean-center columns before cumsum algebra (Pearson corr is shift-invariant — exact math, pure precision win). Without centering, ~100-magnitude rebased prices lose ~1e-7 to cancellation in `n*Sxx - Sx*Sx`; centered keeps errors ~1e-13.
- float64 throughout. After `round(4)` for the DB, cumsum-vs-incremental differences are invisible except last-digit flips on exact rounding boundaries (harmless).

## 4. Partition-key chunking against OOM (CPU and GPU)

- **Chunk by the table's HASH partition key** (`industry_id`, `code`, ...) so a key's rows are NEVER split across chunks, and construct output frames KEY-MAJOR (sorted by partition key) so each chunk is a contiguous run.
- **Per-key frames bound peak memory**: one industry's DataFrame (~90K rows) + its dict list at a time, instead of the full 15.4M-row list (~GBs). Sanitize + write + release each key's frame before building the next.
- **GPU side**: keep tensors per (pool, window) — a (1700, 94, 94) float64 tensor ≈ 120 MB; the full stacked working set ≈ 6 tensors. Check free VRAM via `cp.cuda.runtime.memGetInfo()` and cap usage at 75% of FREE (leaves headroom for concurrent processes). On CuPy `MemoryError`, degrade to the pandas CPU path (concurrent processes can shrink free VRAM between check and allocation).
- **Rationale for key-chunks vs date-chunks**: routing is per-row and order-independent (COPY hashes each row's key O(1)); rows arrive date-major from the emit path so date-chunks are free BUT key-chunks align with the semantic unit, keep PK-ascending order per partition ((date, ...) leading PK), balance chunk sizes, and spread concurrent writes evenly across all hash partitions. Use date-chunks only for (sec_type, code, date)-PK tables where the shared helper `batched_copy_by_date` already applies.

## 5. Bulk DB writes: COPY + partition-key arrangement

- **Shared helper**: `_common.db_commons.batched_copy_by_key_async(conn, table, rows, key="industry_id", label=...)` — groups rows by key, sorts key-major, accumulates whole-key chunks (~100K rows target), one `copy_insert_async` per chunk.
- **COPY vs upsert**:
  - Force mode (table pre-TRUNCATEd → no conflicts possible): chunked COPY — 5–10× faster than INSERT...ON CONFLICT (binary protocol, no per-row conflict arbitration).
  - Incremental mode (non-empty table): `copy_or_upsert_split_async` (COPY fast-path for new dates, ON CONFLICT upsert for gaps).
- **Sort result key-major before sanitize** (`result.sort_values(["industry_id", "date", "pool_size"])`) so chunks stream contiguous key runs.
- **Sanitize per key-frame, not one giant concat**: `sanitize_for_db_insert(frame, numeric_cols=[...], round_to=4)` — inf→NaN→None conversion + dict extraction, bounded to one key's rows.
- **Hash partitioning does NOT speed COPY itself** (only modestly: shallower per-partition btrees). The COPY win is protocol-level. Partitioning pays off in query pruning and maintenance.
- **PK convention**: partition key (code/industry_id) FIRST in the PK for hash partitioning; date-first PKs for date-chunked tables.

## Working example (the correlations pipeline)

```
load mean_close (datetime64) 
→ per pool: pivot to wide (date × industry)
→ overlap = valid.T @ valid                       # one matmul
→ per industry: emit mask + base frame            # numpy bookkeeping
→ per window: pairwise_rolling_corr(wide, W)      # CuPy tensor, one pass
   └ mean-center → joint-valid masks → cumsum window sums →
     3 degenerate guards → |corr|≤1 clamp → D2H
→ assign corr cols by fancy indexing per frame
→ sanitize per frame → batched_copy_by_key_async  # whole-industry chunks
```

Validated: 15,444,338 rows, 4 pools, byte-identical to the original per-pair loop (modulo documented degenerate-window quirks); 1230 s → 467 s wall (remaining time is DB write + emit-path CPU fallbacks on object dtypes, which are inherent at the asyncpg boundary).

## Validation checklist for any new GPU op

1. Cell-by-cell equivalence vs the pandas reference on synthetic data with: NaN heads, interior holes, all-NaN column, constant column, stale (frozen-value) column.
2. Hard invariants: |corr| ≤ 1 + 1e-9; NaN on degenerate windows; counts/sums match per pool.
3. Production-scale force run + row-level diff against a backup table (`CREATE TABLE ... AS SELECT`), max |diff| ≤ last-digit-flip bound (0.0005 after round(4)).
4. Timing comparison at realistic scale (T×N) to confirm the GPU path actually wins.
