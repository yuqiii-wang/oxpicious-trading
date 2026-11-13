/**
 * Futures routes — GET /api/futures/products and GET /api/futures/combined.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync } from "../lib/http.js";
import * as futuresService from "../services/market/futures.service.js";

const router = Router();

// GET /api/futures/products — list available futures products
router.get("/products", wrapAsync(async (_req: Request, res: Response) => {
  const products = await futuresService.listProducts();
  res.json({ products });
}));

// GET /api/futures/combined?product=IF — full combined response
router.get("/combined", wrapAsync(async (req: Request, res: Response) => {
  const product = String(req.query.product ?? "IF");
  const combined = await futuresService.getCombined(product);
  res.json(combined);
}));

export { router };
