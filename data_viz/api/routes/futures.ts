/**
 * Futures routes — GET /api/futures/products and GET /api/futures/combined.
 */
import { Router, type Request, type Response } from "express";
import * as futuresService from "../services/futures.service.js";

const router = Router();

// GET /api/futures/products — list available CFFEX products
router.get("/products", async (_req: Request, res: Response) => {
  try {
    const products = await futuresService.listProducts();
    res.json({ products });
  } catch (err) {
    console.error("[futures/products] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/futures/combined?product=IF — full combined response
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const product = String(req.query.product ?? "IF");
    const combined = await futuresService.getCombined(product);
    res.json(combined);
  } catch (err) {
    console.error("[futures/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export { router };