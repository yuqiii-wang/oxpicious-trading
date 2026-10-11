/**
 * DateBreakdownTable — the selected day's monitor.external_resource_stats
 * buckets, one expandable row per (service, status_code): requests,
 * failures, avg / max whole-call duration, and the service's config count.
 * Expanding a row shows the service's external-request config rows (the
 * static JSON inventory — per-request-type pacing / anti-bot) and fetches
 * the raw monitor.external_resource_audit rows behind that
 * (service, date, status) bucket on first expand (cached per bucket for
 * the component's life — same fetch-on-first-expand pattern as the
 * stream page's run history). The config inventory is fetched once for
 * the whole table (one small row per request type per service) and keyed
 * by service.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import {
  fetchExtResourceAudit,
  fetchExtResourceConfigs,
  type ExtRequestConfigEntry,
  type ExtResourceAuditEntry,
  type ExtResourceStatEntry,
} from "@/lib/api-client/monitor";
import { fmtTimeWithZoneShanghai } from "@/shared/datetime";

const AUDIT_LIMIT = 100;

/** Fetch state of one expanded bucket's audit rows. */
interface BucketAuditState {
  loading: boolean;
  error: string | null;
  entries: ExtResourceAuditEntry[];
}

const NO_AUDIT: BucketAuditState = { loading: false, error: null, entries: [] };

function bucketKey(service: string, statusCode: number | null): string {
  return `${service}|${statusCode ?? "none"}`;
}

/** Status chip color by HTTP class; null = no response (worst). */
function statusColor(statusCode: number | null): "success" | "warning" | "error" {
  if (statusCode == null) return "error";
  if (statusCode >= 200 && statusCode < 300) return "success";
  if (statusCode >= 400) return "error";
  return "warning";
}

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/** "20 ±30%" style sleep cell: base seconds plus jitter as a ± percentage. */
function fmtSleep(sec: number | null, jitter: number | null): string {
  if (sec == null) return "—";
  return jitter == null ? `${sec}` : `${sec} ±${Math.round(jitter * 100)}%`;
}

/** The service's request-config inventory, shown inside an expanded row
 *  ahead of the raw audit sample. */
function ServiceConfigs({ configs }: { configs: ExtRequestConfigEntry[] }) {
  return (
    <Box sx={{ mb: 1.5 }}>
      <Typography variant="caption" color="text.secondary">
        Request configs ({configs.length}) — the pacing / anti-bot cadence
        this service runs with, per request type (config of record migrated
        from code constants).
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Request type</TableCell>
            <TableCell>Type</TableCell>
            <TableCell align="right">Sleep</TableCell>
            <TableCell align="right">Poll (s)</TableCell>
            <TableCell align="right">Retries</TableCell>
            <TableCell align="right">Backoff (s)</TableCell>
            <TableCell align="right">Timeout (c/r)</TableCell>
            <TableCell>Notes</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {configs.map((c) => (
            <TableRow key={c.requestType}>
              <TableCell sx={{ fontFamily: "monospace" }}>
                {c.requestType}
              </TableCell>
              <TableCell>{c.resourceType ?? "?"}</TableCell>
              <TableCell align="right">
                {fmtSleep(c.sleepSec, c.sleepJitter)}
              </TableCell>
              <TableCell align="right">
                {c.pollIntervalSec == null ? "—" : c.pollIntervalSec}
              </TableCell>
              <TableCell align="right">
                {c.maxRetries == null ? "—" : c.maxRetries}
              </TableCell>
              <TableCell align="right">
                {c.retryBackoffSec == null ? "—" : c.retryBackoffSec}
              </TableCell>
              <TableCell align="right">
                {c.timeoutConnectSec == null && c.timeoutReadSec == null
                  ? "—"
                  : `${c.timeoutConnectSec ?? "—"}/${c.timeoutReadSec ?? "—"}`}
              </TableCell>
              <TableCell>
                <Tooltip title={`${c.notes ?? ""}\n[${c.sourceRef}]`}>
                  <Typography
                    variant="caption"
                    sx={{
                      display: "inline-block",
                      maxWidth: 320,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      verticalAlign: "bottom",
                    }}
                  >
                    {c.notes ?? c.sourceRef}
                  </Typography>
                </Tooltip>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

function BucketRow({
  date,
  row,
  open,
  onToggle,
  audit,
  configs,
}: {
  date: string;
  row: ExtResourceStatEntry;
  open: boolean;
  onToggle: () => void;
  audit: BucketAuditState;
  configs: ExtRequestConfigEntry[];
}) {
  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer" }}>
        <TableCell sx={{ fontFamily: "monospace" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {open ? (
              <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
            ) : (
              <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
            )}
            {row.service}
          </Box>
        </TableCell>
        <TableCell>
          <Chip
            size="small"
            color={statusColor(row.statusCode)}
            label={row.statusCode ?? "no response"}
          />
        </TableCell>
        <TableCell align="right">{row.requestCount}</TableCell>
        <TableCell
          align="right"
          sx={{ color: row.failedCount > 0 ? "error.main" : undefined }}
        >
          {row.failedCount}
        </TableCell>
        <TableCell align="right">{fmtMs(row.avgDurationMs)}</TableCell>
        <TableCell align="right">{fmtMs(row.maxDurationMs)}</TableCell>
        <TableCell align="right">
          {configs.length > 0 ? configs.length : "—"}
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={7} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5 }}>
            {configs.length > 0 && <ServiceConfigs configs={configs} />}
            {audit.loading && <CircularProgress size={16} sx={{ my: 1 }} />}
            {audit.error != null && (
              <Typography variant="caption" color="error">
                {audit.error}
              </Typography>
            )}
            {!audit.loading && audit.error == null && audit.entries.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                No audit rows for {row.service} on {date} (status{" "}
                {row.statusCode ?? "no response"}) — the audit writer may have
                dropped them (DB outage) or the stats are stale.
              </Typography>
            )}
            {audit.entries.length > 0 && (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Time</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell>Source</TableCell>
                    <TableCell>URL</TableCell>
                    <TableCell align="right">Status</TableCell>
                    <TableCell align="right">Duration</TableCell>
                    <TableCell>Result</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {audit.entries.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell>
                        {fmtTimeWithZoneShanghai(e.createdAt)}
                      </TableCell>
                      <TableCell>{e.resourceType ?? "?"}</TableCell>
                      <TableCell>{e.subType ?? "?"}</TableCell>
                      <TableCell
                        sx={{
                          fontFamily: "monospace",
                          fontSize: "0.7rem",
                          wordBreak: "break-all",
                          maxWidth: 420,
                        }}
                      >
                        {e.url}
                      </TableCell>
                      <TableCell align="right">{e.statusCode ?? "—"}</TableCell>
                      <TableCell align="right">{fmtMs(e.durationMs)}</TableCell>
                      <TableCell>
                        {e.isFailed ? (
                          <Tooltip title={e.failureReason ?? "failed"}>
                            <Typography
                              variant="caption"
                              color="error"
                              sx={{
                                display: "inline-block",
                                maxWidth: 260,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                                verticalAlign: "bottom",
                              }}
                            >
                              {e.failureReason ?? "failed"}
                            </Typography>
                          </Tooltip>
                        ) : (
                          <Typography variant="caption" color="text.secondary">
                            ok
                          </Typography>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {audit.entries.length >= AUDIT_LIMIT && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5 }}
              >
                Showing the newest {AUDIT_LIMIT} audit rows.
              </Typography>
            )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export default function DateBreakdownTable({
  date,
  rows,
}: {
  date: string;
  rows: ExtResourceStatEntry[];
}) {
  // One expandable row per (service, status_code) bucket; audit rows are
  // fetched once per bucket on first expand and cached for the table's
  // life (a failed fetch is NOT cached — the next expand retries). The
  // request-config inventory is fetched once for the whole table and keyed
  // by service (configs belong to the service, not the status bucket).
  const [openKeys, setOpenKeys] = useState<ReadonlySet<string>>(new Set());
  const [auditByBucket, setAuditByBucket] = useState<Map<string, BucketAuditState>>(
    new Map(),
  );
  const fetchedRef = useRef(new Set<string>());
  const [configsByService, setConfigsByService] = useState<
    Map<string, ExtRequestConfigEntry[]>
  >(new Map());
  // Services without their own config file run the repo-wide default
  // (60s anti-bot cooldown) — the API's defaultRows fallback.
  const [defaultConfigs, setDefaultConfigs] = useState<ExtRequestConfigEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchExtResourceConfigs()
      .then(({ rows, defaultRows }) => {
        if (cancelled) return;
        const byService = new Map<string, ExtRequestConfigEntry[]>();
        for (const row of rows) {
          const list = byService.get(row.service);
          if (list) list.push(row);
          else byService.set(row.service, [row]);
        }
        setConfigsByService(byService);
        setDefaultConfigs(defaultRows);
      })
      .catch((e: unknown) => {
        // Configs are supplementary context — surface it in the console and
        // keep the table fully usable (counts show "—").
        console.warn("ext-resource configs fetch failed:", String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback(
    (row: ExtResourceStatEntry) => {
      const key = bucketKey(row.service, row.statusCode);
      setOpenKeys((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
      if (fetchedRef.current.has(key)) return;
      fetchedRef.current.add(key);
      setAuditByBucket((prev) =>
        new Map(prev).set(key, { loading: true, error: null, entries: [] }),
      );
      fetchExtResourceAudit(row.service, date, row.statusCode, AUDIT_LIMIT)
        .then((entries) =>
          setAuditByBucket((prev) =>
            new Map(prev).set(key, { loading: false, error: null, entries }),
          ),
        )
        .catch((e: unknown) => {
          fetchedRef.current.delete(key); // allow a retry on next expand
          setAuditByBucket((prev) =>
            new Map(prev).set(key, { loading: false, error: String(e), entries: [] }),
          );
        });
    },
    [date],
  );

  const totalRequests = rows.reduce((a, r) => a + r.requestCount, 0);
  const totalFailed = rows.reduce((a, r) => a + r.failedCount, 0);

  return (
    <Paper sx={{ overflowX: "auto" }}>
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 0.5,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">Breakdown — {date} (GMT)</Typography>
        <Typography variant="caption" color="text.secondary">
          GMT day — runs Beijing 08:00 → next 08:00 · {rows.length} bucket(s)
          · {totalRequests.toLocaleString()} requests ·{" "}
          {totalFailed.toLocaleString()} failed · click a row for its request
          configs and the raw audit
        </Typography>
      </Box>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Service</TableCell>
            <TableCell>Status</TableCell>
            <TableCell align="right">Requests</TableCell>
            <TableCell align="right">Failed</TableCell>
            <TableCell align="right">Avg duration</TableCell>
            <TableCell align="right">Max duration</TableCell>
            <TableCell align="right">Configs</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const key = bucketKey(row.service, row.statusCode);
            return (
              <BucketRow
                key={key}
                date={date}
                row={row}
                open={openKeys.has(key)}
                onToggle={() => toggle(row)}
                audit={auditByBucket.get(key) ?? NO_AUDIT}
                configs={configsByService.get(row.service) ?? defaultConfigs}
              />
            );
          })}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} align="center">
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ my: 2, display: "inline-block" }}
                >
                  No stats buckets for this date — run python -m
                  database.external_resource_stats to refresh.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
}
