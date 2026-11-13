/** One chain segment of the display graph: the per-segment lane build
 *  (the plan circle, the step/wave columns, the terminal seat, the
 *  supervisor circle and the spawn-spur pass). The module contract doc
 *  lives on ./types; the layout constants on ./footprints. */
import type {
  AgentLlmContext,
  AgentNodeState,
  AgentThreadDetail,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";
import type {
  ThreadGraphEdge,
  ThreadGraphModel,
  ThreadGraphNode,
  ThreadGraphNodeKind,
  ThreadNodeStatus,
} from "./types";
import {
  SPAWN_TO_SPAWN_OFFSET,
  GAP_COL,
  Y_STEP,
  spawnColumnOffset,
  halfFootprint,
} from "./footprints";
import { contentBoundsOf } from "./items";
import {
  buildUsageByContext,
  countsFragment,
  ctxTail,
  spawnMetrics,
  metricsTail,
  stateElapsedS,
} from "./segmentMetrics";

/** One chain segment's build inputs — the segment's own states and
 *  store items beside the detail-level shared lookups. */
interface SegmentInput {
  index: number;
  last: boolean;
  graphType: "plan" | "custom" | "result" | "single";
  /** This segment's state rows (its thread's slice of the chain). */
  states: AgentThreadState[];
  /** This segment's store node items (runner names already namespaced
   *  by the service for index > 0). */
  nodeStatesRows: AgentNodeState[];
  /** The MERGED contexts (payload lookups are id-keyed — global). */
  contexts: AgentThreadDetail["contexts"];
  /** The merged LLM-context registry. */
  llmContexts: AgentThreadDetail["llm_contexts"];
  /** The merged per-context LLM traffic rollup (token consumptions). */
  llmUsage: AgentThreadDetail["llm_usage"];
  /** The EXPANDED canvas node ids ("step-<id>" / "spawn-<id>") — a
   *  parent whose spawn spurs render. Absent = folded (the default:
   *  the smallest level hides and its spawn column reserves no room). */
  expanded: ReadonlySet<string>;
  /** The chain-level flags (any-segment activity / final result). */
  active: boolean;
  hasFinalResult: boolean;
  hasGraphOutput: boolean;
}

/** One segment's build output — the model plus the chain-wiring
 *  handles (the segment's canvas x-range and its entry/exit node ids,
 *  the handoff edge's endpoints). */
interface SegmentModel extends ThreadGraphModel {
  firstNodeId: string;
  terminalId: string;
  minX: number;
  maxX: number;
}

function buildSegmentModel(input: SegmentInput): SegmentModel {
  const { index: segIdx, last, graphType } = input;
  const states = input.states;
  // Top-level = the model-planned steps; the planning task (the
  // ordinal-0 node 'plan' row every run begins with) is separated out —
  // it rides the plan runner node, never the step chain.
  const topLevel = states
    .filter((s) => s.parent_state_id === null && s.node !== "plan")
    .sort((a, b) => a.ordinal - b.ordinal || a.state_id - b.state_id);
  const planTask =
    states.find(
      (s) => s.parent_state_id === null && s.node === "plan",
    ) ?? null;

  const childrenByParent = new Map<number, AgentThreadState[]>();
  for (const s of states) {
    if (s.parent_state_id !== null) {
      const siblings = childrenByParent.get(s.parent_state_id) ?? [];
      siblings.push(s);
      childrenByParent.set(s.parent_state_id, siblings);
    }
  }
  // The expand/fold registry, keyed by the parent's CANVAS node id: a
  // top-level state hangs as "step-<id>", a child row as "spawn-<id>"
  // or — an INHERIT-context child (child_kind 1, 2026-11-13) —
  // "fork-<id>"; the option builder draws the ▸N/▾N cue and the view
  // toggles on exactly these ids. The prefix follows the PARENT row's
  // own kind (a fork circle's children key under its fork- id).
  // THE DRIFT-FORK FLIP (2026-11-13): a TOP-LEVEL step's fork children
  // render INLINE in the main lane (the forked chain IS the main line)
  // and the abandoned pre-fork tail (child_kind 2) renders as its own
  // always-on demoted spur — neither is a foldable spur of the parent,
  // so the fold counts and every spur/subtree measure go through
  // ``spurKids``, which excludes them.
  const childCountByNodeId = new Map<string, number>();
  const isTopLevelState = new Map<number, boolean>();
  const childKindByStateId = new Map<number, number | null>();
  for (const s of states) {
    isTopLevelState.set(s.state_id, s.parent_state_id === null);
    childKindByStateId.set(s.state_id, s.child_kind);
  }
  // Filled by the lane walk as it decides each single-step run (a
  // wave member's forks keep the spur rendering); consulted by
  // spurKids — declared here so the measures and the fold counts see
  // the same filter.
  const inlineForkStates = new Set<number>();
  const spurKids = (parentId: number): AgentThreadState[] =>
    (childrenByParent.get(parentId) ?? []).filter((c) => {
      if (c.child_kind === 2) return false;
      if (c.child_kind === 1 && inlineForkStates.has(c.state_id)) {
        return false;
      }
      return true;
    });
  for (const [parentId] of childrenByParent) {
    const prefix = isTopLevelState.get(parentId)
      ? "step-"
      : childKindByStateId.get(parentId) === 1
        ? "fork-"
        : "spawn-";
    childCountByNodeId.set(`${prefix}${parentId}`, spurKids(parentId).length);
  }

  const nodeStates = new Map<string, AgentNodeState>();
  const nodeStatesByStateId = new Map<number, AgentNodeState>();
  for (const ns of input.nodeStatesRows) {
    nodeStates.set(ns.node, ns);
    if (ns.state_id !== null) nodeStatesByStateId.set(ns.state_id, ns);
  }
  const contextsById = new Map<string, AgentLlmContext>();
  for (const ctx of input.llmContexts) contextsById.set(ctx.context_id, ctx);
  // The step-input payloads by context id — the wave marks (the
  // planner's parallel assertion / delegation goal) live there.
  const inputPayloads = new Map<number, unknown>();
  for (const c of input.contexts) inputPayloads.set(c.context_id, c.payload);

  const statesActive = states.some(
    (s) => s.status === "pending" || s.status === "in_progress",
  );
  const nodeRunning = input.nodeStatesRows.some((s) => s.status === "running");
  const active = statesActive || nodeRunning;

  // Runner-lane statuses: the store's REAL node state when present
  // (running maps to the display vocabulary's in_progress), else the
  // derived fallback for store-predated threads.
  const runnerStatus = (
    node: string,
    fallback: ThreadNodeStatus,
  ): ThreadNodeStatus => {
    const ns = nodeStates.get(node);
    if (ns === undefined) return fallback;
    return ns.status === "running" ? "in_progress" : ns.status;
  };
  // The segment's per-context usage rollup — the metrics helpers
  // (./segmentMetrics) read it by context id.
  const usageByContext = buildUsageByContext(input.llmUsage);

  const planNs = nodeStates.get("plan");
  // The plan circle's live phase: the store item says 'running' while
  // the proposal LLM call is thinking; on store-less threads the
  // planning task's own row carries the phase.
  const planStatus = runnerStatus(
    "plan",
    planTask !== null
      ? planTask.status
      : states.length > 0
        ? "completed"
        : "failed",
  );
  // THE SEGMENT-ID NAMESPACE — chain segment k>0 prefixes its runner
  // node ids with "s<k>:" (the same convention the store's node names
  // and the output seats follow): the chain concatenates the segments'
  // nodes verbatim, and ECharts' graph series dedupes data items by
  // id — bare "plan"/"execute"/"result" on every legacy lane would
  // collide into one node and crash the chart's init (the store-less
  // degraded chain draws three legacy lanes). Segment 0 keeps the bare
  // ids (the single-segment legacy canvas contract).
  const segPrefix = segIdx === 0 ? "" : `s${segIdx}:`;
  const planNodeId = `${segPrefix}plan`;
  const executeNodeId = `${segPrefix}execute`;
  const resultNodeId = `${segPrefix}result`;

  // The mode: step-chain threads carry node:step-* items; legacy (and
  // degraded — the plain execute lane) threads carry node:execute. A
  // thread with NO store items yet is a steps-mode thread inside its
  // planning window while live (its items land within instants of the
  // first row) and legacy only once settled (pre-store threads).
  const hasStepItems = input.nodeStatesRows.some((ns) =>
    ns.node.startsWith("step-"),
  );
  const hasExecuteItem = input.nodeStatesRows.some(
    (ns) => ns.node === "execute",
  );
  const mode: "steps" | "legacy" =
    hasStepItems ||
    (!hasExecuteItem && (input.nodeStatesRows.length > 0 || active))
      ? "steps"
      : "legacy";

  // THE PLAN CIRCLE: the chain's ROOT segment (and every legacy
  // single-thread run) opens on it; the custom/result segments carry no
  // plan row and no node:plan item — their lane starts at their first
  // step (the previous graph's HANDOFF edge is the entry).
  const drawPlan =
    segIdx === 0 || planTask !== null || planNs !== undefined;

  const nodes: ThreadGraphNode[] = [];
  const edges: ThreadGraphEdge[] = [];
  if (segIdx === 0) {
    nodes.push({ id: "start", kind: "endpoint", label: "START",
      status: "neutral", x: 0, y: 0 });
  }
  if (drawPlan) {
    if (segIdx === 0) edges.push({ source: "start", target: planNodeId });
    nodes.push({
      id: planNodeId, kind: "runner", label: "plan",
      sublabel: planNs !== undefined
        ? (planNs.status === "running"
            ? "planning…"
            : planNs.status === "completed"
              ? `${planNs.n_states} steps planned · ${planNs.n_llm_calls} llm`
              : planNs.status === "failed" ? "no plan (proposal failed)"
              : "no plan")
          + ctxTail(planNs.llm_context_id)
        : planTask !== null
          ? (planTask.status === "in_progress" ? "planning…" : "plan settled")
          : states.length > 0 ? `${topLevel.length} steps planned` : "no plan",
      metrics: planNs !== undefined
        ? metricsTail(planNs.elapsed_s, planNs.llm_context_id, usageByContext)
        : planTask !== null
          ? metricsTail(stateElapsedS(planTask), planTask.llm_context_id, usageByContext)
          : "",
      status: planStatus,
      // START dot (when this is the root) → base gap → the plan's own
      // half-footprint — measured like every lane column.
      x: halfFootprint("endpoint") + GAP_COL + halfFootprint("runner"),
      y: 0,
    });
  }

  // THE LANE CURSOR — a trailing EDGE in abstract units: after each
  // placed column it holds the x that column's footprint ends at (its
  // own half-footprint, extended past the spawn column's outer label
  // edge when the column carries spurs). The next column's x is the
  // edge plus GAP_COL and the next node's half-footprint — every
  // spacing here is measured from the footprint registry, and a parent
  // column is placed AFTER the spawn subtrees whose measured spans size
  // it. A segment without the plan circle (custom / result) opens on
  // an invisible ENTRY DOT at x=0 — the previous graph's HANDOFF edge
  // lands on it (and a wave opening the segment spawns from it).
  let columnEdge: number;
  let firstNodeId: string;
  if (drawPlan) {
    firstNodeId = planNodeId;
    columnEdge = halfFootprint("endpoint") + GAP_COL + halfFootprint("runner")
      + halfFootprint("runner");
  } else {
    firstNodeId = `s${segIdx}-in`;
    nodes.push({ id: firstNodeId, kind: "endpoint", label: "",
      status: "neutral", x: 0, y: 0 });
    columnEdge = halfFootprint("endpoint");
  }

  // Deepest canvas row below the lane so far — wave members, their
  // bends and (in the spawn-spur pass below) the spawn columns all fold
  // in; the model exposes it as maxSpawnRows for the height budget.
  let maxRow = 0;

  // Pre-laid rows per SPAWN column x: a wave seeds the ENTRY node's
  // spawn column (one spawn-column offset right of the entry) with its deepest member row, so a
  // spawned step directly before a wave hangs its spurs below the
  // wave's crossing spawn/merge legs instead of on them.
    const spawnColumnSeeds = new Map<number, number>();
    // THE DRIFT-FORK FLIP: the lane walk's inline fork nodes (child
    // row + placed node + the owning step's id) — the spur pass hangs
    // the fork's OWN children and the abandoned pre-fork tail off them.
    const inlineForks: Array<{
      child: AgentThreadState;
      node: ThreadGraphNode;
      parentId: number;
    }> = [];

  // The lane cursor: the id of the last placed lane node, and a just-
  // laid-out wave's members awaiting their merge target — the wave's
  // left side spawns from prev, its right side merges into the NEXT
  // lane node (the next step, or the terminal). The row-0 member merges
  // on the lane; deeper members merge through right-angle bends at the
  // target's x: each bent path is a vertical leg at the entry/exit
  // node's x plus a horizontal leg at the member's row — a right
  // triangle with the lane (the graph series draws straight segments,
  // so an invisible bend node is the corner; the entry/exit node's
  // label flips above to keep the vertical leg clear).
  let prev = firstNodeId;
  let pendingMerges: {
    straightId: string;
    bends: { id: string; row: number }[];
  } | null = null;
  const mergeInto = (id: string): void => {
    if (pendingMerges === null) {
      edges.push({ source: prev, target: id });
      return;
    }
    edges.push({ source: pendingMerges.straightId, target: id });
    const exitNode = nodes.find((n) => n.id === id);
    if (exitNode !== undefined) exitNode.labelTop = true;
    const exitX = exitNode?.x ?? 0;
    pendingMerges.bends.forEach(({ id: src, row }) => {
      const bendId = `bend-merge-${src.slice("step-".length)}`;
      nodes.push({
        id: bendId, kind: "bend", label: "", status: "neutral",
        x: exitX, y: row * Y_STEP,
      });
      edges.push({ source: src, target: bendId, kind: "bare" });
      edges.push({ source: bendId, target: id });
    });
    pendingMerges = null;
  };
  const laneNext = (id: string): void => {
    mergeInto(id);
    prev = id;
  };

  if (mode === "legacy") {
    const executeX = columnEdge + GAP_COL + halfFootprint("runner");
    columnEdge = executeX + halfFootprint("runner");
    const executeNs = nodeStates.get("execute");
    nodes.push({
      id: executeNodeId, kind: "runner", label: "execute",
      sublabel: (executeNs !== undefined
        ? (executeNs.status === "running"
            ? `running · ${countsFragment(
                executeNs.n_llm_calls, executeNs.n_tool_calls,
                executeNs.llm_context_id, usageByContext)}`
            : executeNs.status === "failed" ? "agent loop failed"
            : `${executeNs.n_llm_calls} llm · ${executeNs.n_tool_calls} tools · ${executeNs.n_states} tasks`)
          + ctxTail(executeNs.llm_context_id)
        : active ? "agent loop running" : `${topLevel.length} steps walked`),
      metrics: executeNs !== undefined
        ? metricsTail(executeNs.elapsed_s, executeNs.llm_context_id, usageByContext)
        : "",
      status: runnerStatus("execute", active ? "in_progress" : "completed"),
      x: executeX, y: 0,
    });
    edges.push({ source: prev, target: executeNodeId });
    prev = executeNodeId;
  } else {
    // THE PLAN AS THE NODE CHAIN: every top-level state (including
    // removed/skipped ones — the chain is the audit) in ordinal order.
    // One step's canvas circle. A DELEGATED step ran no main
    // conversation — its circle shows the counts of the spawns
    // (children) that did the work, read from their LLM-context
    // registry items.
    const pushStepNode = (
      state: AgentThreadState, x: number, y: number,
    ): string => {
      const id = `step-${state.state_id}`;
      const ns = nodeStatesByStateId.get(state.state_id);
      const ctxId = state.llm_context_id ?? ns?.llm_context_id ?? null;
      const spawnIds = (childrenByParent.get(state.state_id) ?? []).map(
        (c) => c.llm_context_id,
      );
      let nLlm = ns?.n_llm_calls ?? 0;
      let nTools = ns?.n_tool_calls ?? 0;
      if (ns !== undefined && ns.llm_context_id === null) {
        for (const cid of spawnIds) {
          if (cid === null) continue;
          const ctx = contextsById.get(cid);
          if (ctx !== undefined) {
            nLlm += ctx.n_llm_calls;
            nTools += ctx.n_tool_calls;
          }
        }
      }
      nodes.push({
        id,
        kind: "step",
        label: `${state.ordinal}. ${state.node}`,
        sublabel: countsFragment(nLlm, nTools, ctxId, usageByContext)
          + ctxTail(ctxId),
        // Elapsed + tokens: the step's own context when it ran one, else
        // the aggregated spawns' (a delegated step ran no conversation).
        metrics: ctxId !== null
          ? metricsTail(
              ns?.elapsed_s ?? stateElapsedS(state),
              ctxId,
              usageByContext,
            )
          : spawnMetrics(state.state_id, childrenByParent, usageByContext),
        status: state.status,
        x,
        y,
      });
      return id;
    };
    // The step's wave mark: its step-input payload carries
    // parallel: true (the planner's independence assertion) or a
    // delegate goal — the same flags the runner's wave detector reads
    // (planning.store fetch_step_delegate). Consecutive marked steps
    // ran as ONE concurrent wave.
    const waveMarked = (s: AgentThreadState): boolean => {
      if (s.input_context_id === null) return false;
      const payload = inputPayloads.get(s.input_context_id);
      if (payload === null || typeof payload !== "object") return false;
      const p = payload as Record<string, unknown>;
      return p.parallel === true ||
        (typeof p.delegate_goal === "string" && p.delegate_goal !== "");
    };
    // ---- THE BOTTOM-UP MEASURE PASS — every VISIBLE spawn subtree's
    // row span, computed post-order from the LEAVES (the smallest
    // nodes) before any placement: a leaf spawn spans its own row, a
    // parent spawn's span sums its children's measured spans. A child's
    // own subtree counts only when THAT child's spawn node is expanded
    // (the fold default hides the smallest level), and the caller only
    // consults a state's span when the state's own node is expanded —
    // so a folded subtree reserves no room at all. Wave member rows
    // and the spawn-column cursors are cut from these measured spans,
    // so a subtree's room is reserved where it hangs, under the parent
    // that owns it.
    const subtreeRowsMemo = new Map<number, number>();
    const subtreeRows = (stateId: number): number => {
      const memo = subtreeRowsMemo.get(stateId);
      if (memo !== undefined) return memo;
      let rows = 0;
      for (const child of spurKids(stateId)) {
        const expandedId = `${child.child_kind === 1 ? "fork-" : "spawn-"}`
          + `${child.state_id}`;
        rows += 1 + (input.expanded.has(expandedId)
          ? subtreeRows(child.state_id)
          : 0);
      }
      subtreeRowsMemo.set(stateId, rows);
      return rows;
    };
    // The WIDTH twin of subtreeRows — the state's rendered spawn
    // subtree's horizontal reach FROM the state's own column center to
    // its deepest spawn column CENTER: each visible child indents one
    // spawn-to-spawn offset right of its parent, and only an EXPANDED
    // child adds its own subtree's reach (the fold default hides the
    // deeper levels). Same caller contract as subtreeRows: consulted
    // only when the state's own node is expanded, so a folded subtree
    // reserves no room at all.
    const subtreeWidthMemo = new Map<number, number>();
    const subtreeWidth = (stateId: number): number => {
      const memo = subtreeWidthMemo.get(stateId);
      if (memo !== undefined) return memo;
      let width = 0;
      for (const child of spurKids(stateId)) {
        const expandedId = `${child.child_kind === 1 ? "fork-" : "spawn-"}`
          + `${child.state_id}`;
        width = Math.max(
          width,
          SPAWN_TO_SPAWN_OFFSET
            + (input.expanded.has(expandedId)
              ? subtreeWidth(child.state_id)
              : 0),
        );
      }
      subtreeWidthMemo.set(stateId, width);
      return width;
    };

    let i = 0;
    while (i < topLevel.length) {
      // The head step always joins the run; when it is wave-marked the
      // run extends over the consecutive wave-marked steps after it
      // (the runner's parallel wave) — either way the run is non-empty
      // and the cursor always advances.
      let j = i + 1;
      if (waveMarked(topLevel[i])) {
        while (j < topLevel.length && waveMarked(topLevel[j])) j += 1;
      }
      const run = topLevel.slice(i, j);
      i = j;
      // THE DRIFT-FORK FLIP: a SINGLE step's fork children (child_kind
      // 1) render INLINE in the main lane — registered here, before
      // the column's measures run, so spurKids/subtreeRows/subtreeWidth
      // already exclude them (the fork reserves its own lane width
      // below; it is main line, not a spur). A wave member's forks
      // keep the spur rendering.
      if (run.length === 1) {
        for (const c of childrenByParent.get(run[0].state_id) ?? []) {
          if (c.child_kind === 1) inlineForkStates.add(c.state_id);
        }
      }
      // The column's x: the previous column's trailing edge plus the
      // base gap and this column's half-footprint (the 84px name block
      // dominates the 44px circle). Then the edge extends past the
      // spawn column's outer label edge when the column carries spurs —
      // the NEXT column clears the spawn labels by GAP_COL, and the
      // wave spawn/merge bend legs drop at that next column's x, off
      // the labels.
      const colHalf = halfFootprint("step");
      const x = columnEdge + GAP_COL + colHalf;
      // A column reserves spawn room only when one of its steps' spurs
      // actually RENDER — an expanded parent. A folded step's column
      // is lane-tight: the main path keeps its proper spacing instead
      // of reserving gaps for hidden subtrees. The reserved width is
      // DEPTH-AWARE (subtreeWidth): each expanded level indents one
      // spawn-to-spawn offset further right, so a multi-level subtree
      // extends the column edge past its DEEPEST spawn column, not just
      // the first.
      const runSpawnWidth = run.reduce((w, s) => {
        if (!input.expanded.has(`step-${s.state_id}`)) return w;
        if (spurKids(s.state_id).length === 0) return w;
        return Math.max(
          w, spawnColumnOffset("step") + subtreeWidth(s.state_id));
      }, 0);
      columnEdge = runSpawnWidth > 0
        ? x + runSpawnWidth + halfFootprint("spawn")
        : x + colHalf;
      if (run.length === 1) {
        const stepId = pushStepNode(run[0], x, 0);
        laneNext(stepId);
        // THE DRIFT-FORK FLIP: the step's fork children render INLINE —
        // the forked chain IS the main line (kept to the left), and
        // every later node, wave, terminal and bound recomputes after
        // it. The parent → fork chain edge upgrades to the thick fork
        // style (the inheritance cue); the abandoned pre-fork tail
        // (child_kind 2) renders in the spur pass, demoted right.
        for (const fc of childrenByParent.get(run[0].state_id) ?? []) {
          if (fc.child_kind !== 1) continue;
          const forkHalf = halfFootprint("fork");
          const fx = columnEdge + GAP_COL + forkHalf;
          const forkExpanded = input.expanded.has(`fork-${fc.state_id}`);
          const forkW = forkExpanded ? subtreeWidth(fc.state_id) : 0;
          columnEdge = forkW > 0
            ? fx + forkW + halfFootprint("spawn")
            : fx + forkHalf;
          const forkNode: ThreadGraphNode = {
            id: `fork-${fc.state_id}`,
            kind: "fork",
            label: "fork",
            // The fork's goal (the child row's title) — the canvas
            // identity of the recovered purpose.
            sublabel: fc.title,
            metrics: metricsTail(
              stateElapsedS(fc),
              fc.llm_context_id,
              usageByContext,
            ),
            status: fc.status,
            x: fx, y: 0,
          };
          nodes.push(forkNode);
          laneNext(forkNode.id);
          const upstream = edges[edges.length - 1];
          if (upstream.source === stepId
              && upstream.target === forkNode.id) {
            upstream.kind = "fork";
          }
          inlineForks.push({
            child: fc, node: forkNode, parentId: run[0].state_id,
          });
        }
      } else {
        // THE PARALLEL WAVE COLUMN: the members share one x and stack
        // vertically — same-size step circles running concurrently.
        // Tight one-apart while nothing spawns; a member that carries
        // spawn spurs pushes the NEXT member below its whole subtree,
        // so the spurs interleave between member rows — never ON one
        // (a merge leg crosses the spawn column at every member row).
        // The spawn legs are RIGHT ANGLES: the row-0 member hangs on
        // the lane (plain chain edge); every deeper member's spawn
        // drops at the entry's x and turns right at ITS row through
        // an invisible bend node — a right triangle with the lane.
        // The merge legs mirror this at the next lane node
        // (mergeInto). No member-to-member chain — they ran side by
        // side, not in sequence.
        const entryNode = nodes.find((n) => n.id === prev);
        if (entryNode !== undefined) entryNode.labelTop = true;
        const entryX = entryNode?.x ?? 0;
        // Fold-aware member stacking: a member reserves the rows under
        // itself only when its spurs render (it is expanded); a folded
        // member is one row — the wave stacks tight.
        const memberRows: number[] = [];
        let nextRow = 0;
        for (const state of run) {
          memberRows.push(nextRow);
          nextRow += 1 + (input.expanded.has(`step-${state.state_id}`)
            ? subtreeRows(state.state_id)
            : 0);
        }
        const memberIds = run.map(
          (state, k) => pushStepNode(state, x, memberRows[k] * Y_STEP),
        );
        maxRow = Math.max(maxRow, memberRows[memberRows.length - 1]);
        memberIds.forEach((id, k) => {
          if (k === 0) {
            edges.push({ source: prev, target: id });
            return;
          }
          const bendId = `bend-spawn-${run[k].state_id}`;
          nodes.push({
            id: bendId, kind: "bend", label: "", status: "neutral",
            x: entryX, y: memberRows[k] * Y_STEP,
          });
          edges.push({ source: prev, target: bendId, kind: "bare" });
          edges.push({ source: bendId, target: id });
        });
        pendingMerges = {
          straightId: memberIds[0],
          bends: memberIds.slice(1).map((id, k) => ({
            id, row: memberRows[k + 1],
          })),
        };
        // A spawned step directly before this wave hangs its spurs in
        // the column one spawn-column offset right of the entry — below
        // the wave's deepest member row, off the crossing legs. Only
        // when those spurs render (the entry is expanded).
        if (entryNode !== undefined && input.expanded.has(entryNode.id)) {
          const entrySpawnX = entryX
            + spawnColumnOffset(entryNode?.kind ?? "endpoint");
          spawnColumnSeeds.set(
            entrySpawnX,
            Math.max(
              spawnColumnSeeds.get(entrySpawnX) ?? -1,
              memberRows[memberRows.length - 1],
            ),
          );
        }
      }
    }
    // THE FOLD COUNTS REFIT after the lane walk: inlineForkStates is
    // filled DURING the walk (a single step's fork children render
    // inline), so the counts — first computed before it — recompute
    // over the final spur sets (the ▸N cue counts foldable spurs only).
    childCountByNodeId.clear();
    for (const [parentId] of childrenByParent) {
      const prefix = isTopLevelState.get(parentId)
        ? "step-"
        : childKindByStateId.get(parentId) === 1
          ? "fork-"
          : "spawn-";
      childCountByNodeId.set(`${prefix}${parentId}`,
                             spurKids(parentId).length);
    }
  }

  // No finalize circle: the runner's settle phase is bookkeeping, not a
  // stop worth a node — the lane runs straight into the TERMINAL.
  // THE TERMINAL — the graph's FINAL OUTPUT NODE when the segment's
  // store carries node:output items (every 2026-11-06+ graph; the
  // custom graph's seat writes the graph-output handoff row, the
  // result graph's the final-result answer), else the legacy result
  // node (pre-refactor threads and degraded runs). The plan segment's
  // seat writes no store item — its artifact is the sealed plan
  // payload, so its status derives from the planning task's row.
  const outputName = segIdx === 0 ? "output" : `s${segIdx}:output`;
  const outputNs = nodeStates.get(outputName);
  // The plan segment's output seat writes no store item (its artifact
  // is the sealed plan payload) — its lane is the new one regardless,
  // its status derived from the planning task's row below.
  const isNewLane = outputNs !== undefined || graphType === "plan";
  // The terminal's x: the last lane column's trailing edge (already
  // extended past its spawn labels) plus the base gap and the seat's
  // half-footprint — the same measured advance as between lane steps,
  // so the two-line output label and its predecessor's clear by GAP_COL
  // at the fit floor by construction.
  const terminalKind: ThreadGraphNodeKind = isNewLane ? "output" : "runner";
  const resultX = columnEdge + GAP_COL + halfFootprint(terminalKind);
  let terminalId: string;
  if (isNewLane) {
    terminalId = outputName;
    const tStatus: ThreadNodeStatus = outputNs !== undefined
      ? (outputNs.status === "running" ? "in_progress" : outputNs.status)
      : planTask !== null && planTask.status !== "in_progress"
        ? planTask.status
        : active ? "in_progress" : "skipped";
    const sub =
      graphType === "plan"
        ? (planTask !== null && planTask.status === "completed"
            ? "plan sealed → custom graph"
            : planTask !== null && planTask.status === "in_progress"
              ? "sealing the plan…"
              // A failed plan no longer ends the run (2026-11-07 the
              // result graph runs on degraded runs too — the chain is
              // plan + result): the seat hands off to the degraded
              // execute lane, not to a custom graph.
              : "no plan — degraded execute")
        : last
          ? (input.hasFinalResult ? "final answer recorded"
             : input.active ? "waiting for the final answer"
             : "no result recorded")
          : (input.hasGraphOutput ? "findings synthesis → result graph"
             : input.active ? "synthesizing the findings…"
             : "no synthesis recorded");
    nodes.push({
      id: terminalId, kind: "output", label: "output",
      // The plan segment's seat writes no store item — no context tail.
      sublabel: sub + ctxTail(outputNs?.llm_context_id ?? null),
      metrics: metricsTail(
        outputNs?.elapsed_s ?? null,
        outputNs?.llm_context_id ?? null,
        usageByContext,
      ),
      status: tStatus,
      x: resultX, y: 0,
    });
  } else {
    terminalId = resultNodeId;
    nodes.push({
      id: resultNodeId, kind: "runner", label: "result",
      // The legacy graph's LAST node — its fixed output (the kind
      // final-result context) is what the Result view displays.
      sublabel: input.hasFinalResult
        ? "final answer recorded"
        : active ? "waiting for the answer" : "no result recorded",
      status: runnerStatus(
        "result",
        input.hasFinalResult ? "completed" : active ? "pending" : "skipped"),
      x: resultX, y: 0,
    });
  }
  let endX = resultX;
  if (last) {
    endX = resultX + halfFootprint(terminalKind) + GAP_COL
      + halfFootprint("endpoint");
    nodes.push({ id: "end", kind: "endpoint", label: "END",
      status: "neutral", x: endX, y: 0 });
  }
  // ---- THE SUPERVISOR — an INDEPENDENT WATCHDOG beside the graphs
  // (the orchestrator runs the ONE session beside the whole chain; it
  // is NOT a graph node and deliberately carries NO PATH to the
  // graphs — no edge in either direction): ONE free-floating circle
  // ABOVE the lane over this segment's terminal (the root segment
  // only — its store item lives there). IDLE on healthy runs — the
  // canvas label says so ("idle — no root-path failure", zero LLM) —
  // and it wakes ONLY when a root-path node failed: ONE bounded call
  // in its own context whose diagnosis + plan/topology verdict lands
  // as the kind supervisor-review context (the sublabel carries the
  // verdict line). While the run is live the circle reads "watching
  // the main path…". Threads that predate the supervisor (and degraded
  // execute-lane runs) carry no node:supervisor item and draw none.
  // When a wave ended the lane, the terminal's label flips above for
  // the merge bends, so the supervisor climbs one extra row to keep
  // that label zone clear.
  const supervisorNs = nodeStates.get("supervisor");
  // The intervention review's verdict line ("Overall: <verdict>")
  // from the kind supervisor-review payload — absent on idle runs.
  const reviewPayload = input.contexts.find(
    (c) => c.kind === "supervisor-review",
  )?.payload;
  const reviewText =
    reviewPayload !== null && typeof reviewPayload === "object"
      && !Array.isArray(reviewPayload)
      ? (reviewPayload as Record<string, unknown>).review
      : null;
  const supervisorVerdict = typeof reviewText === "string"
    ? reviewText.match(/Overall:\s*([a-z-]+)/i)?.[1]?.toLowerCase() ?? null
    : null;
  if (supervisorNs !== undefined && mode === "steps" && segIdx === 0) {
    nodes.push({
      id: "supervisor", kind: "supervisor", label: "supervisor",
      sublabel: supervisorNs.status === "running"
        ? "watching the main path…"
        : supervisorNs.status === "failed"
          ? "intervention failed"
          : supervisorNs.status === "skipped"
            ? "no intervention"
            : supervisorVerdict !== null
              ? `review: ${supervisorVerdict} · `
                + `${supervisorNs.n_llm_calls} llm`
                + ctxTail(supervisorNs.llm_context_id)
              // Completed with no review row = the watchdog ran its
              // code sweep, found no root-path failure, stayed IDLE.
              : `idle — no root-path failure${ctxTail(supervisorNs.llm_context_id)}`,
      metrics: metricsTail(supervisorNs.elapsed_s, supervisorNs.llm_context_id, usageByContext),
      status: supervisorNs.status === "running"
        ? "in_progress" : supervisorNs.status,
      x: resultX,
      y: -(pendingMerges !== null ? 2 : 1) * Y_STEP,
      labelTop: true,
    });
  }

  // A wave that ended the lane merges ALL its members into the
  // terminal (right-angle bends at its x); otherwise the last lane
  // node chains in. The supervisor never joins these — it has no path
  // to the graphs.
  mergeInto(terminalId);
  if (last) edges.push({ source: terminalId, target: "end" });

  // ---- SPAWN SPURS (steps mode): every spawned subagent branch (and
  // anything the subagent itself added under its spawn) draws in a SPAWN
  // COLUMN one spawn-column offset to the RIGHT of its parent — each
  // spawn circle horizontally after and vertically below its parent,
  // wired by a RIGHT-ANGLE two-way pair: a solid spawn edge drops at
  // the parent's x, turns right at the spawn's row through an invisible
  // bend node and arrows into the spawn; the dashed MERGE-BACK edge
  // retraces the same elbow up into the parent (the parent's
  // conversation gathered the spawn's result; the gathered answers also
  // surface in the clicked parent's context section) — one L-shaped
  // two-way connector per spur, fanning out of the parent's vertical
  // trunk like an org chart. The parent's label flips ABOVE the circle
  // (the trunk would cross the below-text). NESTED branches continue
  // DOWNWARD and one spawn-to-spawn offset to the RIGHT of their own
  // parent spawn (a slightly-right column per depth level) through a
  // per-column row cursor, depth-first: each next spawn lands below
  // everything placed so far in its column (a subtree ends above the
  // next sibling spawn) and at least one row below its own parent.
  // THE FOLD GATE: a parent renders its spurs only when its canvas
  // node id is expanded — folded by default (the smallest level hides;
  // its column reserved no room in the lane pass above), a parent
  // click expands one level and the layout reflows around it.
  const statesById = new Map<number, AgentThreadState>();
  for (const s of states) statesById.set(s.state_id, s);
  if (mode === "steps") {
    const columnRow = new Map<number, number>(spawnColumnSeeds);
    const addSpawns = (
      parentNode: ThreadGraphNode, parentStateId: number, columnX: number,
    ): void => {
      if (!input.expanded.has(parentNode.id)) return;
      // spurKids: the inline forks (a top-level step's fork children
      // render IN the lane above) and the abandoned pre-fork tails
      // (always-on demoted spurs off their fork node, rendered below)
      // never fan out here.
      const children = spurKids(parentStateId);
      if (children.length === 0) return;
      // The parent's spawn trunk drops straight down from its circle —
      // flip its label above so the trunk doesn't cross the below-text
      // (the same flip a wave entry/exit makes for its merge legs).
      parentNode.labelTop = true;
      for (const child of children) {
        // The child's row clears BOTH cursors: its own spawn column's
        // (where the child circle lands) AND the parent's column's
        // (where this spur's elbow bend lands at parentNode.x). The
        // bend is invisible but its legs are not — a sibling spawn
        // already sitting below the parent in the parent's column must
        // never be overrun, or the bend coincides with its circle and
        // this spur reads as a SECOND PARENT EDGE on that sibling
        // (run 4597f052: bend-spur-2826 landed exactly on spawn-2827,
        // so the probe spawn looked parented by both its step and the
        // failed sibling above it). Bumping the parent's column cursor
        // here also pushes the NEXT sibling below this whole subtree —
        // depth-first, a subtree ends above the next sibling spawn.
        const row = Math.max(
          (columnRow.get(columnX) ?? -1) + 1,
          (columnRow.get(parentNode.x) ?? -1) + 1,
          Math.round(parentNode.y / Y_STEP) + 1,
        );
        columnRow.set(columnX, row);
        columnRow.set(parentNode.x, row);
        maxRow = Math.max(maxRow, row);
        // THE CHILD'S KIND (2026-11-13): child_kind 1 = a FORK — an
        // inherit-context child (it continued a deep copy of the
        // parent's transcript). It draws on the same spur geometry as
        // a spawn, wearing the fork tag, and its FORWARD legs render
        // THICKER than a spawn's (the inheritance cue; the option
        // builder's fork branch owns the weight).
        const isFork = child.child_kind === 1;
        const spawnNode: ThreadGraphNode = {
          id: `${isFork ? "fork" : "spawn"}-${child.state_id}`,
          kind: isFork ? "fork" : "spawn",
          label: isFork ? "fork" : "spawn",
          // The spawn's goal (the child row's title) — the canvas
          // identity of what the parent delegated/gathered.
          sublabel: child.title,
          metrics: metricsTail(
            stateElapsedS(child),
            child.llm_context_id,
            usageByContext,
          ),
          status: child.status,
          x: columnX,
          y: row * Y_STEP,
        };
        nodes.push(spawnNode);
        // THE RIGHT-ANGLE SPUR — one invisible bend at the parent's x
        // on the spawn's row turns the pair's shared two-way connector
        // into an L: the solid spawn legs run parent ↓ bend → spawn
        // (arrowhead into the spawn), the dashed merge-back retraces
        // spawn ← bend ↑ parent (arrowhead into the parent); the legs
        // INTO the corner carry no arrowhead (the wave paths' bare-leg
        // rule — the bend is wiring, not a stop). A fork's forward legs
        // ride the fork/fork_lead kinds — same geometry, heavier line.
        const bendId = `bend-spur-${child.state_id}`;
        nodes.push({
          id: bendId, kind: "bend", label: "", status: "neutral",
          x: parentNode.x, y: spawnNode.y,
        });
        edges.push({
          source: parentNode.id, target: bendId,
          kind: isFork ? "fork_lead" : "bare",
        });
        edges.push({
          source: bendId, target: spawnNode.id,
          kind: isFork ? "fork" : "spawn",
        });
        edges.push({ source: spawnNode.id, target: bendId, kind: "bare" });
        edges.push({ source: bendId, target: parentNode.id, kind: "merge" });
        addSpawns(
          spawnNode, child.state_id, spawnNode.x + SPAWN_TO_SPAWN_OFFSET);
      }
    };
    for (const state of topLevel) {
      const node = nodes.find((n) => n.id === `step-${state.state_id}`);
      if (node !== undefined) {
        addSpawns(node, state.state_id, node.x + spawnColumnOffset(node.kind));
      }
    }
    // THE DRIFT-FORK FLIP's spur pass: each INLINE fork node (main
    // lane) hangs its OWN children fan (fold-gated, fork- ids), and
    // its parent's ABANDONED pre-fork tail rows (child_kind 2) render
    // as always-on demoted spurs off the fork — the OLD chain pushed to
    // the right-side edge path, one dashed thin leg, grey/skipped.
    for (const { child, node: forkNode, parentId } of inlineForks) {
      addSpawns(forkNode, child.state_id,
                forkNode.x + spawnColumnOffset("fork"));
      for (const ab of childrenByParent.get(parentId) ?? []) {
        if (ab.child_kind !== 2) continue;
        const columnX = forkNode.x + spawnColumnOffset("fork");
        const row = Math.max(
          (columnRow.get(columnX) ?? -1) + 1,
          (columnRow.get(forkNode.x) ?? -1) + 1,
          Math.round(forkNode.y / Y_STEP) + 1,
        );
        columnRow.set(columnX, row);
        columnRow.set(forkNode.x, row);
        maxRow = Math.max(maxRow, row);
        const oldNode: ThreadGraphNode = {
          id: `old-${ab.state_id}`,
          kind: "abandoned",
          label: "old chain",
          sublabel: ab.title.replace(/^\[old chain\]\s*/, ""),
          metrics: metricsTail(
            stateElapsedS(ab),
            ab.llm_context_id,
            usageByContext,
          ),
          status: ab.status,
          x: columnX,
          y: row * Y_STEP,
        };
        nodes.push(oldNode);
        edges.push({
          source: forkNode.id, target: oldNode.id, kind: "abandoned",
        });
      }
    }
  }

  return {
    threadId: states[0]?.thread_id ?? "",
    nodes,
    edges,
    active,
    childrenByParent,
    childCountByNodeId,
    statesById,
    maxSpawnRows: maxRow,
    contentBounds: contentBoundsOf(nodes),
    topLevelStates: topLevel,
    planTask,
    nodeStates,
    nodeStatesByStateId,
    contextsById,
    mode,
    segments: [],
    firstNodeId,
    terminalId,
    minX: 0,
    maxX: endX,
  };
}

export { buildSegmentModel };
