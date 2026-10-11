/**
 * DirPathFilterSelect — toolbar dropdown for the config tables: filter rows
 * by the NESTED DIRECTORY names of the services' dotted tree paths
 * ("downloads.etf.huatai_pb.trend" lives in dir downloads/etf/huatai_pb/).
 *
 * The options are the tree's real directories: every dotted prefix that some
 * service extends ("builds.etf" is a dir because builds.etf.feeder_nav
 * exists), sorted so parents sit right before their children, each annotated
 * with its subtree row count. Selecting a dir keeps the rows under it — plus
 * the dir's own file row when one exists (builds.etf under builds/etf).
 * "(all)" is the no-filter state; the component hides itself when the tree
 * has no nested dirs at all.
 *
 * `maxDepth` caps the listed dirs at that many dotted segments — deeper
 * dirs stay reachable through their ancestor's subtree filter.
 */
import { useMemo } from "react";
import { Box, MenuItem, TextField, Typography } from "@mui/material";

/** Every nested directory path of the given dotted service names — each
 * proper prefix of each name (a name's last segment is the file), at most
 * `maxDepth` segments deep when given. */
export function buildDirPaths(services: string[], maxDepth?: number): string[] {
  const set = new Set<string>();
  for (const svc of services) {
    const parts = svc.split(".");
    const depth = Math.min(parts.length - 1, maxDepth ?? parts.length - 1);
    for (let i = 1; i <= depth; i++) {
      set.add(parts.slice(0, i).join("."));
    }
  }
  return [...set].sort();
}

/** Is `service` inside `dir`'s subtree? dir "" = no filter (everything is). */
export function serviceInDir(service: string, dir: string): boolean {
  return dir === "" || service === dir || service.startsWith(`${dir}.`);
}

interface DirPathFilterSelectProps {
  /** All loaded service names (dotted tree paths). */
  services: string[];
  /** Selected directory (dotted path) — "" = all. */
  value: string;
  onChange: (next: string) => void;
  /** Cap listed dirs at this many dotted segments (undefined = no cap). */
  maxDepth?: number;
}

export default function DirPathFilterSelect({
  services,
  value,
  onChange,
  maxDepth,
}: DirPathFilterSelectProps) {
  const dirs = useMemo(() => buildDirPaths(services, maxDepth), [services, maxDepth]);
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const d of dirs) {
      map.set(d, services.filter((s) => serviceInDir(s, d)).length);
    }
    return map;
  }, [dirs, services]);

  if (dirs.length === 0) return null;

  return (
    <TextField
      select
      size="small"
      label="Directory"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      // Closed state shows the path only — the subtree count lives in the
      // open menu's rows.
      SelectProps={{
        renderValue: (v: string) =>
          v === "" ? (
            <Typography variant="caption" color="text.secondary">
              (all)
            </Typography>
          ) : (
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {v}
            </Typography>
          ),
      }}
      sx={{ width: 240 }}
    >
      <MenuItem value="">
        <Typography variant="caption" color="text.secondary">
          (all)
        </Typography>
      </MenuItem>
      {dirs.map((d) => (
        <MenuItem key={d} value={d}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {d}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="caption" color="text.secondary">
              {counts.get(d)}
            </Typography>
          </Box>
        </MenuItem>
      ))}
    </TextField>
  );
}
