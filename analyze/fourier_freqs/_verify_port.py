"""Port audit: recompute the consolidated pattern score in Python for the
EXACT window the UI displayed (stored spectrum last_date for 000300,
range_days=750) and compare against the browser-rendered value
("top score ≈ 58d (0.058)").

Checks the full chain the frontend runs:
  1. merged day amps from the STORED amplitude_spectrum (JS dayMap merge:
     day = round(N/k), merged amp = sqrt(sum amp_k^2)),
  2. sigma_band = sqrt(sum_{d<=N/4} amp_d^2 / 2),
  3. recEXT (prominence-filtered extrema evidence),
  4. acfFrac (MA-detrended ACF recurrence),
  5. score = ampNorm * recEXT * acfFrac.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import psycopg2
from scipy.signal import find_peaks

TOL = 0.15
K_PROM = 1.5

conn = psycopg2.connect(host="127.0.0.1", port=9876, dbname="oxpicious-stats",
                        user="postgres", password="postgres")
cur = conn.cursor()

# 1) stored spectrum row for the UI window
cur.execute("""
    SELECT last_date, amplitude_spectrum FROM analysis.fourier_freqs
    WHERE code = '000300' AND sec_type = 'index' AND range_days = 750
    ORDER BY last_date DESC LIMIT 1
""")
last_date, spectrum = cur.fetchone()
spectrum = np.asarray(spectrum, dtype=np.float64)
N = 750
print(f"stored spectrum: last_date={last_date} n_bins={len(spectrum)}")

# 2) closes ending exactly at last_date (the UI slice)
cur.execute("""
    SELECT close FROM stats.index_basic_stats
    WHERE code = '000300' AND date <= %s AND close IS NOT NULL
    ORDER BY date DESC LIMIT %s
""", (last_date, N))
w = np.array([r[0] for r in cur.fetchall()], dtype=np.float64)[::-1]
cur.close()
conn.close()
print(f"window closes: N={len(w)} end={last_date}")

# ---- JS dayMap merge on the stored spectrum ---------------------------
ks = np.arange(1, len(spectrum) + 1)
days = np.round(N / ks).astype(int)
merged = np.sqrt(np.bincount(days, weights=spectrum**2))
day_index = np.unique(days)
amp_by_day = dict(zip(day_index, merged[day_index]))

# ---- factors (same math as _study_consolidated) ------------------------
sig_band = float(np.sqrt(sum(v**2 for d, v in amp_by_day.items() if d <= N // 4) / 2.0))

sd = float(np.std(np.diff(w)))
hi, _ = find_peaks(w, prominence=K_PROM * sd)
lo, _ = find_peaks(-w, prominence=K_PROM * sd)
idx = np.concatenate([hi, lo])
typ = np.concatenate([np.ones(len(hi)), -np.ones(len(lo))])
order = np.argsort(idx)
idx, typ = idx[order], typ[order]
if len(idx) > 1:
    idx = idx[np.concatenate([[True], np.diff(typ) != 0])]
gaps = np.diff(idx).astype(float) if len(idx) > 1 else np.array([])
pool = (np.concatenate([gaps[:-1] + gaps[1:], 2 * gaps]) if len(gaps) > 1
        else np.array([]))

Nr = len(w)
L = max(3, (Nr // 4) | 1)
half = L // 2
cs = np.concatenate([[0.0], np.cumsum(w)])
ma = np.array([(cs[min(Nr, i + half + 1)] - cs[max(0, i - half)]) /
               (min(Nr, i + half + 1) - max(0, i - half)) for i in range(Nr)])
resid = w - ma
xc = resid - resid.mean()
denom = float(np.dot(xc, xc))
acf = np.correlate(xc, xc, mode="full")[Nr - 1:] / denom
tau = 1.96 / np.sqrt(Nr)

rows = []
for d in sorted(day_index):
    if d < 2 or d > Nr // 2 or d > Nr // 3:
        continue
    ev = float(np.sum(np.abs(pool - d) <= TOL * d))
    maxrep = max((Nr - d) // d, 1)
    recEXT = min(ev / maxrep, 1.0)
    m = np.arange(1, (Nr - d) // d + 1)
    acf_frac = float((acf[m * d] >= tau).mean()) if len(m) else 0.0
    amp_norm = amp_by_day[d] / sig_band
    rows.append((d, amp_norm, recEXT, acf_frac, amp_norm * recEXT * acf_frac, ev, maxrep))

print(f"\nsigma_band={sig_band:.1f} extrema={len(idx)} pool={len(pool)}")
print("-- top-5 by score (Python recompute of the UI window) --")
for d, an, re_, af, s, ev, mr in sorted(rows, key=lambda r: -r[4])[:5]:
    print(f"  {d:4d}d  score={s:6.3f}  ampNorm={an:5.2f} recEXT={re_:5.2f} "
          f"acfFrac={af:5.2f}  ev={ev:5.0f}/{mr}")
print("-- probe 39d/58d --")
for d, an, re_, af, s, ev, mr in rows:
    if d in (39, 58):
        print(f"  {d:4d}d  score={s:6.3f}  ampNorm={an:5.2f} recEXT={re_:5.2f} "
              f"acfFrac={af:5.2f}  ev={ev:5.0f}/{mr}")

# ---- JS-port extrema semantics (strict local extrema + manual
# prominence, as in patternScore.ts) — explains any small delta vs scipy
def js_prominence(x, i, sign):
    h = sign * x[i]
    left_min = h
    for j in range(i - 1, -1, -1):
        v = sign * x[j]
        if v > h:
            break
        if v < left_min:
            left_min = v
    right_min = h
    for j in range(i + 1, len(x)):
        v = sign * x[j]
        if v > h:
            break
        if v < right_min:
            right_min = v
    return h - max(left_min, right_min)

sd0 = float(np.std(np.diff(w)))
prom_thr = K_PROM * sd0
ext = []
for i in range(1, Nr - 1):
    if w[i] > w[i - 1] and w[i] > w[i + 1]:
        if js_prominence(w, i, 1) >= prom_thr:
            ext.append((i, 1))
    elif w[i] < w[i - 1] and w[i] < w[i + 1]:
        if js_prominence(w, i, -1) >= prom_thr:
            ext.append((i, -1))
ext.sort()
kept = []
for e in ext:
    if not kept or kept[-1][1] != e[1]:
        kept.append(e)
gaps_js = [kept[i][0] - kept[i - 1][0] for i in range(1, len(kept))]
pool_js = ([gaps_js[i - 1] + gaps_js[i] for i in range(1, len(gaps_js))] +
           [2 * g for g in gaps_js]) if len(gaps_js) > 1 else []
pool_js = np.asarray(pool_js, dtype=np.float64)

rows_js = []
for d in sorted(day_index):
    if d < 2 or d > Nr // 2 or d > Nr // 3:
        continue
    ev = float(np.sum(np.abs(pool_js - d) <= TOL * d))
    maxrep = max((Nr - d) // d, 1)
    recEXT = min(ev / maxrep, 1.0)
    m = np.arange(1, (Nr - d) // d + 1)
    acf_frac = float((acf[m * d] >= tau).mean()) if len(m) else 0.0
    amp_norm = amp_by_day[d] / sig_band
    rows_js.append((d, amp_norm, recEXT, acf_frac, amp_norm * recEXT * acf_frac, ev, maxrep))

print(f"\n-- JS-port semantics: extrema={len(kept)} pool={len(pool_js)} "
      f"(scipy: extrema={len(idx)} pool={len(pool)}) --")
print("-- top-5 by score (JS-port extrema semantics) --")
for d, an, re_, af, s, ev, mr in sorted(rows_js, key=lambda r: -r[4])[:5]:
    print(f"  {d:4d}d  score={s:6.3f}  ampNorm={an:5.2f} recEXT={re_:5.2f} "
          f"acfFrac={af:5.2f}  ev={ev:5.0f}/{mr}")
