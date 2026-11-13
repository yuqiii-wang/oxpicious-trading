/** True when the canvas node id is one of the chain's FINAL OUTPUT
 *  nodes (or the legacy result node — bare on segment 0, "s<k>:result"
 *  on the chain's later legacy lanes) — the context panel reads their
 *  kind final-result / graph-output documents. */
export function isOutputNodeId(nodeId: string): boolean {
  return (
    nodeId === "result" ||
    nodeId === "output" ||
    /^s\d+:output$/.test(nodeId) ||
    /^s\d+:result$/.test(nodeId)
  );
}

/** The canvas node id stripped of its chain-segment namespace — the
 *  segment-local runner ids ("s1:plan", "s2:execute") resolve to the
 *  same kinds the bare segment-0 ids do. */
export function bareNodeId(nodeId: string): string {
  return nodeId.replace(/^s\d+:/, "");
}

/** The segment index a canvas node id belongs to (0 for bare ids and
 *  step/spawn rows — state ids are globally unique; the s<k>: namespace
 *  marks chain segment k's runner-lane nodes). */
export function segmentIndexOfNodeId(nodeId: string): number {
  const m = nodeId.match(/^s(\d+):/);
  return m === null ? 0 : Number(m[1]);
}
