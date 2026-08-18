/**
 * Band texture generation for Expiry OI Bands — offscreen canvas per
 * put-share bucket (1% steps) for fast O(1) lookup during rendering.
 */
import { DOWN_COLOR, UP_COLOR } from "@/theme/chart-palette";

const BAND_TEX_W = 8;
const BAND_TEX_H = 128;
const BLEND_BAND = 0.28;
const bandTextureCache = new Map<number, HTMLCanvasElement>();

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  if (h.length !== 6) return [0, 0, 0];
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

export function mixHex(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const bl = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r},${g},${bl})`;
}

/**
 * Render one band texture: vertical color gradient (call-green above →
 * put-red below, split at the put-share latitude with a wide blend band),
 * then an alpha gradient that fades BOTH the top and bottom boundaries to
 * transparent so band edges vanish instead of drawing hard horizontal lines.
 *
 * `strength` (0..1) is the OI darkness channel: both side colors are mixed
 * toward white at the same rate, so small-OI bands render as pale pastels
 * while wall-sized OI (width-saturated) bands deepen toward full color.
 */
export function getBandTexture(putPct: number, strength: number): HTMLCanvasElement {
  const putBucket = Math.max(0, Math.min(100, Math.round(putPct)));
  const strBucket = Math.max(0, Math.min(100, Math.round(strength * 100)));
  const cacheKey = putBucket * 101 + strBucket;
  const cached = bandTextureCache.get(cacheKey);
  if (cached) return cached;

  const w = BAND_TEX_W;
  const h = BAND_TEX_H;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const putFrac = putBucket / 100;
  const str = strBucket / 100;
  const upSoft = mixHex("#ffffff", UP_COLOR, str);
  const downSoft = mixHex("#ffffff", DOWN_COLOR, str);

  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  if (putFrac <= 0) {
    ctx.fillStyle = upSoft;
  } else if (putFrac >= 1) {
    ctx.fillStyle = downSoft;
  } else {
    const split = 1 - putFrac;
    const bl = Math.min(BLEND_BAND, putFrac * 0.92, (1 - putFrac) * 0.92);
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, upSoft);
    grad.addColorStop(Math.max(0, split - bl), upSoft);
    grad.addColorStop(Math.min(1, split + bl), downSoft);
    grad.addColorStop(1, downSoft);
    ctx.fillStyle = grad;
  }
  ctx.fill();

  ctx.globalCompositeOperation = "destination-in";
  const fade = ctx.createLinearGradient(0, 0, 0, h);
  fade.addColorStop(0, "rgba(0,0,0,0)");
  fade.addColorStop(0.18, "rgba(0,0,0,0.85)");
  fade.addColorStop(0.5, "rgba(0,0,0,1)");
  fade.addColorStop(0.82, "rgba(0,0,0,0.85)");
  fade.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = fade;
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.fill();
  ctx.globalCompositeOperation = "source-over";

  bandTextureCache.set(cacheKey, canvas);
  return canvas;
}
