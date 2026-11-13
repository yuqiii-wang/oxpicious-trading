/**
 * ETF + Margin API routes.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, intParam } from "../lib/http.js";
import {
  listThemes,
  listStrategyThemes,
  getEtfMarginCombined,
} from "../services/market/etf-margin.service.js";

const router = Router();

router.get("/themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listThemes(exchange));
}));

/** GET /api/etf-margin/strategy-themes — parallel L1 strategy → L2 theme tree */
router.get("/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  const exchange = strParam(req, "exchange");
  res.json(await listStrategyThemes(exchange));
}));

router.get("/combined", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    sector: strParam(req, "sector"),
    industry: strParam(req, "industry"),
    strategy: strParam(req, "strategy"),
    theme: strParam(req, "theme"),
    code: strParam(req, "code"),
    exchange: strParam(req, "exchange"),
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
    limit_per_theme: intParam(req, "limit_per_theme"),
    page: intParam(req, "page"),
    page_size: intParam(req, "page_size"),
  };
  res.json(await getEtfMarginCombined(query));
}));

export default router;
