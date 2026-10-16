/**
 * Options API routes (all venues — SZSE / SSE ETF options and CFFEX index
 * options; the /api/szse-options URL prefix is kept for client stability).
 */
import { Router, type Request, type Response } from "express";
import {
  listUnderlyings,
  getOptionsCombined,
  getOptionsWalls,
  getEtfOhlcv,
  getOptionsSkewnessSeries,
  getOptionsOiStats,
  getOptionsBands,
  getOptionsIvSkew,
  getOptionsVolIndex,
  getOptionsVolIndexCorr,
  getOptionsDates,
  getOptionsSnapshotRows,
  getOptionsSnapshotAxes,
  getOptionsSkewDaily,
  getOptionsOiDaily,
  getOptionsOiCells,
  getOptionsExpiryMeta,
  getOptionsMoneynessSeries,
} from "../services/options.service.js";
import { SKEW_TYPES, type SkewType } from "../../shared/types.js";

const router = Router();

/** Parse + validate the skew_type query param (defaults to oi_moneyness). */
function parseSkewType(raw: unknown): SkewType {
  return typeof raw === "string" && (SKEW_TYPES as string[]).includes(raw)
    ? (raw as SkewType)
    : "oi_moneyness";
}

/** ?fresh=1 — Refresh-button bypass for the version-keyed aggregate cache. */
function wantsFresh(req: Request): boolean {
  return req.query.fresh === "1";
}

router.get("/underlyings", async (req: Request, res: Response) => {
  try {
    const targetType =
      typeof req.query.target_type === "string" ? req.query.target_type : undefined;
    const exchange =
      typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listUnderlyings(targetType, exchange));
  } catch (err) {
    console.error("[szse-options/underlyings] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/combined", async (req: Request, res: Response) => {
  try {
    const query = {
      underlying: typeof req.query.underlying === "string" ? req.query.underlying : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      target_type: typeof req.query.target_type === "string" ? req.query.target_type : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
    };
    res.json(await getOptionsCombined(query));
  } catch (err) {
    console.error("[szse-options/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/walls", async (req: Request, res: Response) => {
  try {
    const query = {
      underlying: typeof req.query.underlying === "string" ? req.query.underlying : undefined,
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    };
    res.json(await getOptionsWalls(query));
  } catch (err) {
    console.error("[szse-options/walls] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/etf-ohlcv", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' query parameter" });
      return;
    }
    const data = await getEtfOhlcv(
      code,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      typeof req.query.target_type === "string" ? req.query.target_type : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/etf-ohlcv] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/skewness-series", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const skewType = parseSkewType(req.query.skew_type);
    const data = await getOptionsSkewnessSeries(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
      skewType,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/skewness-series] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/oi-stats", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const data = await getOptionsOiStats(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/oi-stats] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/bands", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const data = await getOptionsBands(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/bands] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/iv-skew", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const data = await getOptionsIvSkew(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/iv-skew] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Shared query-param parser for the per-plot endpoints. */
function parsePlotQuery(req: Request) {
  return {
    underlying: typeof req.query.underlying === "string" ? req.query.underlying : undefined,
    start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
    end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    target_type: typeof req.query.target_type === "string" ? req.query.target_type : undefined,
    exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
  };
}

/** Per-plot: distinct trading dates + counters (date picker, caption). */
router.get("/dates", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsDates(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: quote rows for one or more specific dates
 *  (?dates=YYYY-MM-DD,YYYY-MM-DD,… — index-narrow per date, no history scan). */
router.get("/snapshot-rows", async (req: Request, res: Response) => {
  try {
    const dates = typeof req.query.dates === "string"
      ? req.query.dates.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    res.json(await getOptionsSnapshotRows(parsePlotQuery(req), dates));
  } catch (err) {
    console.error("[szse-options/snapshot-rows] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: history-wide strike/expiry-month axes (wall panel) — cached
 *  per underlying/venue, never varies with the selected date. */
router.get("/snapshot-axes", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsSnapshotAxes(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/snapshot-axes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: per (date × real expiry) smile anchors (ATM + 25Δ/10Δ wings)
 *  for the Smile Chronology panel's Today AND History views — replaces the
 *  panel's in-browser recompute of the full-history /combined blob. */
router.get("/skew-daily", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsSkewDaily(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/skew-daily] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: per (date × expiry) call/put OI + spot (trend charts). */
router.get("/oi-daily", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsOiDaily(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/oi-daily] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: per (date × expiry × strike) OI cells for the selected
 * expiry cohorts (?expiries=YYYY-MM-DD,YYYY-MM-DD,…). */
router.get("/oi-cells", async (req: Request, res: Response) => {
  try {
    const expiries = typeof req.query.expiries === "string"
      ? req.query.expiries.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    res.json(await getOptionsOiCells(parsePlotQuery(req), expiries));
  } catch (err) {
    console.error("[szse-options/oi-cells] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: per-contract final OI expiry markers (cohort totals are
 *  derived client-side from /oi-daily). */
router.get("/expiry-meta", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsExpiryMeta(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/expiry-meta] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: server-computed oi_moneyness skew series (spot + groups). */
router.get("/oi-moneyness-series", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsMoneynessSeries(parsePlotQuery(req), wantsFresh(req)));
  } catch (err) {
    console.error("[szse-options/oi-moneyness-series] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/vol-index", async (req: Request, res: Response) => {
  try {
    // underlying optional — omitting it returns every underlying's series
    // (cross-asset comparison chart).
    const underlying =
      typeof req.query.underlying === "string" && req.query.underlying
        ? req.query.underlying
        : undefined;
    const data = await getOptionsVolIndex(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/vol-index] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/vol-index-corr", async (req: Request, res: Response) => {
  try {
    // underlying optional — omitting it returns every underlying's series.
    const underlying =
      typeof req.query.underlying === "string" && req.query.underlying
        ? req.query.underlying
        : undefined;
    const data = await getOptionsVolIndexCorr(
      underlying,
      typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    );
    res.json(data);
  } catch (err) {
    console.error("[szse-options/vol-index-corr] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
