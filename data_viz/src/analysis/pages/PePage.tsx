/**
 * Thin re-export so existing imports of `@/analysis/pages/PePage`
 * continue to resolve. The actual implementation lives in the `./Pe/`
 * subdirectory (split into smaller modules, mirroring MaSpread/).
 */
export { default } from "./Pe";
