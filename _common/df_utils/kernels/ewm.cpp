// Grouped EWM (adjust=False, ignore_na=True) — one fused CUDA kernel.
//
// Serial recurrence per contiguous group, ONE THREAD PER GROUP:
//     y_0 = x_first_valid
//     y_t = (1 - a) * y_{t-1} + a * x_t        (valid inputs only)
// NaN inputs are skipped (ignore_na=True): the state (y, cnt) is
// unchanged. PANDAS OUTPUT SEMANTICS at a NaN-input position: the LAST
// weighted average is carried forward (out = y once cnt >= min_periods,
// NaN during warm-up / before the first valid) — pandas ewm.mean does
// NOT emit NaN at NaN-input positions (verified:
// temp_scripts/debug_pandas_ewm_semantics.py).
//
// min_periods semantics match pandas: outputs stay NaN until `cnt`
// (count of valid observations so far, NaNs excluded) reaches
// min_periods; the recurrence itself always runs.
//
// PARITY: the op order `d * y + alpha * v` (d = 1 - alpha) mirrors
// pandas' Cython ewm inner loop exactly, so values are bit-identical
// (max_rel 5e-16 measured) and NaN placement matches pandas' carry-
// forward rule. Verified in temp_scripts/test_ewm_kernel.py vs
// groupby(...).ewm(alpha, adjust=False, min_periods, ignore_na=True).mean()
// incl. NaN gaps, warm-up heads and sub-min_periods groups.
//
// CONTRACT (caller obligations — enforced in the bridge):
//   - x is float64 device memory, NaN-free NOT required (NaN = skip)
//   - groups are CONTIGUOUS blocks; starts/ends are exclusive-bound
//     int64 device arrays, one entry per group
//   - one thread per group; groups with zero rows are not representable
//     (starts[g] < ends[g] always — callers must not emit empty groups)
extern "C" __global__
void grouped_ewm_adjust_false_ignore_na(
    const double* x, const long long* starts, const long long* ends,
    double* out, double alpha, int min_periods, int n_groups)
{
    int g = blockIdx.x * blockDim.x + threadIdx.x;
    if (g >= n_groups) return;
    double d = 1.0 - alpha;
    double y = 0.0;
    long long cnt = 0;
    // runtime-computed NaN (no NAN macro needed under NVRTC)
    double nan_out = 0.0 / 0.0;
    bool ready = false;                  // cnt >= min_periods latch
    for (long long t = starts[g]; t < ends[g]; ++t) {
        double v = x[t];
        if (isnan(v)) {
            // pandas carries the last EWM value through NaN-input rows
            out[t] = ready ? y : nan_out;
            continue;
        }
        y = (cnt == 0) ? v : d * y + alpha * v;
        ++cnt;
        if (!ready && cnt >= (long long)min_periods) ready = true;
        out[t] = ready ? y : nan_out;
    }
}
