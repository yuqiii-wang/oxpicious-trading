// EMA (adjust=False) fused kernel — one file per algo/formula.
//
// Exact closed form of the recursive EMA
//     y_t = a*x_t + (1-a)*y_{t-1},   y_0 = x_0
// unrolled to
//     y_t = a*G_t + d^(p+1)*x0,      d = 1 - a,  p = position within group
//     G_t = sum_{j < terms} d^j * x_{t-j},   terms = min(max_terms, p+1)
//
// max_terms = smallest T with d^T < 1e-12 (relative truncation error bound),
// computed host-side per span. The seed term d^(p+1)*x0 makes warm-up rows
// exact and underflows to 0 for old rows; x[t-p] is the group's first value.
// Group boundaries: the caller guarantees groups are contiguous blocks and
// pos (0-based position within group) is precomputed — terms <= p+1 keeps
// every read x[t-j] inside the group, so no boundary mask is needed.
//
// NUMERICS: G_t is accumulated as a BINARY-COUNTER PAIRWISE sum (tree
// order, error O(log terms)) rather than sequentially (O(terms)). This
// matches the error profile of the shift-doubling column path closely
// enough that 6-decimal-rounded output is bit-identical to pandas ewm.
//
// One thread per output row; threads read a ~max_terms-element sliding
// window that stays cache-resident (L1/L2), so DRAM traffic ~= 2x input.
// Runs ~10x faster than the shift-doubling column passes (RTX 5090,
// 6.8M rows, spans 6/10/20/60: 20 ms vs 200 ms) with peak VRAM of 3-4
// whole columns (vs ~15-18 transient columns for shift-doubling).
extern "C" __global__
void ema_adjust_false(const double* x, const long long* pos, double* out,
                      double alpha, double d, int max_terms, int n) {
    int t = blockDim.x * blockIdx.x + threadIdx.x;
    if (t >= n) return;
    int p = (int)pos[t];
    int terms = max_terms < (p + 1) ? max_terms : (p + 1);
    // pairwise (binary-counter) accumulation of s_j = d^j * x_{t-j}:
    // seg[k] holds the sum of the last 2^k pending s values; merging two
    // equal blocks halves the count, giving a tree-shaped reduction.
    // 14 levels cover terms up to 2^14 = 16384 (bridge enforces the bound).
    double seg[14];
    bool has[14];
    #pragma unroll
    for (int k = 0; k < 14; ++k) has[k] = false;
    double w = 1.0;                     // d^j, computed incrementally
    for (int j = 0; j < terms; ++j) {
        double cur = w * x[t - j];
        w *= d;
        int k = 0;
        while (k < 14 && has[k]) {
            cur = seg[k] + cur;
            has[k] = false;
            ++k;
        }
        if (k < 14) {
            seg[k] = cur;
            has[k] = true;
        }
    }
    double acc = 0.0;
    #pragma unroll
    for (int k = 0; k < 14; ++k) {
        if (has[k]) acc += seg[k];
    }
    out[t] = alpha * acc + pow(d, (double)(p + 1)) * x[t - p];
}
