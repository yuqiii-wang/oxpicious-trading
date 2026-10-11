/**
 * serviceGroup — consolidate the open-ended dotted service vocabulary
 * (monitor.external_resource_audit.service, one entry per pipeline
 * module) into the small set of display groups the trend chart stacks
 * per day. The DB keeps full names (per-process attribution is the
 * table's point); only the chart roll-up narrows them:
 *
 *   downloads.stream.<source>.<kind> → "stream.<source>"  (the always-on
 *       pollers dominate volume — keep the exchange visible)
 *   downloads.<category>.<source>.<kind> → "<category>"   (margin, index,
 *       bond, macro, options, stock, etf, ...)
 *   anything else → its top-level package (llm_agents, analysis, ...)
 *
 * Drilling in (the day breakdown table + raw audit fetch) still uses the
 * exact service names — this helper is chart-display only.
 */

/** Chart-display group for one dotted service name. */
export function serviceGroup(service: string): string {
  const parts = service.split(".");
  if (parts[0] === "downloads" && parts[1] === "stream" && parts.length >= 3) {
    return `stream.${parts[2]}`;
  }
  if (parts[0] === "downloads" && parts.length >= 2) {
    return parts[1];
  }
  return parts[0] ?? service;
}

/** Distinct sorted display groups for a list of service names. */
export function serviceGroups(services: readonly string[]): string[] {
  return [...new Set(services.map(serviceGroup))].sort();
}
