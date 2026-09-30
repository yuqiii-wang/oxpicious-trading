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
  getOptionsIvSkew,
  getOptionsVolIndex,
  getOptionsDates,
  getOptionsSnapshot,
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
    res.json(await getOptionsDates(parsePlotQuery(req)));
  } catch (err) {
    console.error("[szse-options/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: one date's quote rows + unified strike/expiry-month meta. */
router.get("/snapshot", async (req: Request, res: Response) => {
  try {
    const query = parsePlotQuery(req);
    if (!query.start_date && !query.end_date) {
      res.status(400).json({ error: "Missing 'start_date' (snapshot date)" });
      return;
    }
    res.json(await getOptionsSnapshot(query));
  } catch (err) {
    console.error("[szse-options/snapshot] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: per (date × expiry) call/put OI + spot (trend charts). */
router.get("/oi-daily", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsOiDaily(parsePlotQuery(req)));
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

/** Per-plot: per-contract final OI expiry markers + cohort totals. */
router.get("/expiry-meta", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsExpiryMeta(parsePlotQuery(req)));
  } catch (err) {
    console.error("[szse-options/expiry-meta] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** Per-plot: server-computed oi_moneyness skew series (spot + groups). */
router.get("/oi-moneyness-series", async (req: Request, res: Response) => {
  try {
    res.json(await getOptionsMoneynessSeries(parsePlotQuery(req)));
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

export default router;
