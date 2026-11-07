/**
 * graphModel — the LangGraph → display-graph mapping for the Thread
 * Graph view (pure, no React/ECharts imports so it stays unit-clear).
 *
 * The runner (llm_agents/deep_agents/langgraph/runner.py) compiles a StateGraph
 * that executes the plan: the PLAN AGENT SESSION (planning/session.py —
 * since 2026-11-06 OUTSIDE the graph, awaited before ainvoke) studies
 * the business input (scope, focus question, skills, analysis items)
 * and writes the research steps, each of which executes as its OWN
 * graph node hop — one superstep, one LLM conversation per step —
 * routed hop by hop off the LIVING plan tree (a step's update_plan
 * adds/removes/revokes not-yet-run steps and the next hop follows).
 * The plan still opens the lane on this canvas (the plan runner circle
 * is the plan session's own node item) — planning just no longer
 * spends a graph node or a checkpoint.
 *
 * CANVAS = TOPOLOGY ONLY (the LangGraph/LangSmith observability
 * contract): the canvas draws exactly the chain the runtime walked —
 *
 *     plan (the session's circle) → <top-level steps, ordinal order> →
 *             result → END
 *
 * The runner's own finalize settle phase is NOT drawn — it is
 * bookkeeping (no LLM, no answer), so the chain runs the steps
 * straight into result and the settle's outcome reads off result's
 * status.
 *
 * with each step circle wearing its row's status and its sublabel of
 * call counts riding the TOOLTIP (the canvas label is the step name
 * only — the auto-fit scale puts step columns closer together than a
 * two-line label block, so the counts collided with the neighbors'
 * labels; joined through the store's step node item by state_id —
 * one node one context means one conversation per step now). A
 * DELEGATED step ran no main conversation (a supervised subagent was
 * its engine) — its circle shows the child's counts as the fork's.
 *
 * PARALLEL WAVE COLUMN: consecutive DELEGATED-or-PARALLEL steps (the
 * runner's wave — its members run concurrently; the marks ride each
 * step's step-input payload, written at plan time) draw as ONE column
 * of SAME-SIZE step circles STACKED VERTICALLY, wired as a FORK/MERGE
 * diamond with RIGHT-ANGLE PATHS: the entry lane node (plan, or the
 * previous step) forks into EVERY member and every member merges into
 * the NEXT lane node (the next step, or result) — no member-to-member
 * chain, they ran side by side, not in sequence. Each fork/merge path
 * bends at a right angle through an INVISIBLE BEND NODE on the
 * entry/exit node's x — a vertical leg down/up plus a horizontal leg
 * at the member's row, forming a right triangle with the lane (the
 * graph series draws straight segments only; the bend node is the
 * corner). The first leg of a bent path carries no arrowhead (per-link
 * symbol override); the second leg keeps the straight right-triangle
 * arrowhead. Entry/exit nodes flip their labels ABOVE the circle so
 * the vertical leg stays clear. A single marked step (a wave of one)
 * lays out like any other step.
 *
 * Members that fork push the NEXT member down past their whole fork
 * subtree (subtreeRows): the wave column stays tight when nothing
 * forks, but FORKED PARENTS gain the vertical room so their spurs
 * interleave between one member's row and the next — an upper forked
 * parent's sub-sub nodes always stay ABOVE the lower parent node (and
 * off every member row, where the merge legs cross the fork column).
 *
 * FORK SPURS: every spawned subagent branch (and anything the subagent
 * itself added under its fork) draws in a FORK COLUMN one X_FORK to the
 * RIGHT of its parent step — each fork circle sits HORIZONTALLY AFTER
 * and VERTICALLY BELOW its parent: a solid spawn edge down-right, a
 * dashed MERGE-BACK edge up-left (the parent's conversation gathered
 * the fork's result; the gathered answers also surface in the clicked
 * parent's context section) — the pair shares one diagonal two-way
 * segment with an arrowhead at each end. Fork circles are smaller,
 * wear the child state's status, and label to the right with the fork
 * goal. Nested branches continue DOWNWARD in the same fork column —
 * depth-first through a per-column row cursor: each next fork lands
 * below everything placed so far in that column (so a fork subtree
 * always ends above the next sibling fork) and at least one row below
 * its own parent. Wave members share their step column, so they share
 * one fork column too — the member-spacing rule above interleaves
 * their spurs between the member rows.
 *
 * The PLANNING TASK: every run starts with a running planning node —
 * the ordinal-0 top-level state row (node 'plan', in_progress) the
 * daemon's begin_thread writes at run start, closed by the plan agent
 * session (planning.session — OUTSIDE the graph) when the proposal
 * lands. It is NOT a step circle (the model-planned
 * chain is the steps, not the runner's own phase): the plan runner
 * circle wears its status, and clicking it lists the planning task as
 * its own row beneath the graph. While that row is the thread's ONLY
 * state, the graph is the planning window — zero step circles between
 * plan and result.
 *
 * THE SUPERVISOR (runner.py's run_thread tail — the SUPERVISOR
 * WATCHDOG): an INDEPENDENT agent beside the graph, NOT a node of it —
 * the canvas draws ONE free-floating circle ABOVE the lane over result
 * with NO EDGE in either direction (no path to the graph, by design).
 * It is IDLE on healthy runs: the watchdog's trigger is a code filter
 * over the settled node states, and only a ROOT-PATH failure (a
 * failed top-level step, or the failed planning row) wakes it — ONE
 * bounded call in its own context whose diagnosis + plan/topology
 * verdict persists as the kind supervisor-review context (the
 * sublabel carries the verdict). While the run is live the circle
 * reads "watching the main path…"; settled healthy it reads "idle — no
 * root-path failure". When NO node is clicked the beneath-graph panel
 * shows the supervisor by default (its lane state, its one context and
 * the review document when one exists). Threads that predate the
 * supervisor (and degraded execute-lane runs, which never persisted a
 * thread the watchdog sweeps) draw none.
 *
 * LEGACY lane: threads from before the step graph (and degraded runs
 * whose plan never persisted — those ran the one-conversation
 * "execute" lane) carry a node:execute item instead of node:step-*
 * items; for them the canvas keeps the fixed runner chain
 * plan → execute → result → END and the steps
 * render as execute's task list beneath the graph, as before. A thread
 * with NO store items is legacy only once settled — a live thread with
 * no items yet is a steps-mode thread inside its planning window (the
 * store item for the plan session lands within instants of the row).
 *
 * The runner-lane statuses come from the LangGraph STORE when the
 * thread has node-state items (detail.node_states); threads that
 * predate the store keep the DERIVED statuses:
 *
 *   plan      completed when the thread has any state, else failed
 *   result    completed when the kind final-result context exists,
 *             pending while the run is live, else skipped
 *
 * Clicking a circle opens that node's contexts + rows BENEATH the
 * graph (DOM, not canvas): itemsUnderNodeId lists the node's states —
 * a step node's own row (expanding its spawned forks and its
 * input/result payloads), the plan session's planning task, legacy
 * execute's whole task list.
 */
import type {
  AgentLlmContext,
  AgentNodeState,
  AgentThreadDetail,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";

/** Every node's status — state lifecycle statuses plus the derived
 *  runner-lane ones. */
export type ThreadNodeStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed"
  | "skipped"
  | "neutral";

/** Which layer of the graph a canvas node sits on — endpoint (START/
 *  END), runner (the plan/finalize/result lane), output (a graph's
 *  FINAL OUTPUT NODE — the chain's handoff producer), step (a planned
 *  research step that executed as its own node hop), fork (a spawned
 *  subagent branch, drawn in a fork column to the right of and below
 *  its parent), supervisor (the main path's checker — ONE independent
 *  circle ABOVE the lane over result, off the walked chain), caption
 *  (a chain segment's floating title, invisible circle + text label),
 *  or bend (an invisible right-angle corner of a wave fork/merge
 *  path). */
export type ThreadGraphNodeKind =
  | "endpoint"
  | "runner"
  | "output"
  | "step"
  | "fork"
  | "supervisor"
  | "caption"
  | "bend";

export interface ThreadGraphNode {
  /** Stable id — "start" | "plan" | "step-<state_id>" | "supervisor" |
   *  "output" (segment 0's / legacy-final) | "s<k>:output" (chain
   *  segment k's final output node — the service's namespaced store
   *  name) | "result" (legacy lanes) | "end", wave path corners
   *  "bend-fork-<state_id>" / "bend-merge-<state_id>", chain segment
   *  titles "caption-<k>". */
  id: string;
  kind: ThreadGraphNodeKind;
  /** Primary label (the node tag). */
  label: string;
  /** Secondary line under the label — the step's title, or the node's
   *  context id and call counts (one node one context). */
  sublabel?: string;
  status: ThreadNodeStatus;
  /** Layout position (abstract units; ECharts auto-fits the view). */
  x: number;
  y: number;
  /** Label ABOVE the circle instead of below — wave entry/exit nodes
   *  whose below-label a vertical fork/merge leg would cross. */
  labelTop?: boolean;
}

export interface ThreadGraphEdge {
  source: string;
  target: string;
  /** chain — the walked main lane; handoff — the SPECIAL INTER-GRAPH
   *  edge of the three-graph chain (one graph's final output node →
   *  the next graph's entry; drawn as its own distinct thick dashed
   *  double-arrow link — the two graphs read as two graphs, not one
   *  lane of regular nodes); spawn — parent → fork (down); merge —
   *  fork → parent (the dashed merge-back edge); bare — a chain-styled
   *  leg WITHOUT the target arrowhead (the first leg of a bent wave
   *  path — the corner gets no arrow). Absent = chain. */
  kind?: "chain" | "handoff" | "spawn" | "merge" | "bare";
}

/** One chain segment's view metadata — the caption band and the
 *  beneath-the-graph per-segment reads. */
export interface ThreadSegmentView {
  /** The segment's own thread id. */
  threadId: string;
  /** plan | custom | result (single = a legacy / degraded one-thread
   *  run — no chain split). */
  graphType: "plan" | "custom" | "result" | "single";
  /** The segment's canvas x-range (already offset). */
  x0: number;
  x1: number;
}

export interface ThreadGraphModel {
  threadId: string;
  /** The chain's ROOT thread id (the run's super_thread_id; equals
   *  threadId on legacy single-thread runs). */
  superThreadId?: string;
  nodes: ThreadGraphNode[];
  edges: ThreadGraphEdge[];
  /** True while any state is pending/in_progress or a runner-lane node
   *  state (the store's real statuses) is running — the poll-on flag. */
  active: boolean;
  /** Child states grouped by parent state_id (insertion order). */
  childrenByParent: Map<number, AgentThreadState[]>;
  /** Every state row by id — fork spur lookups (a fork is a child row,
   *  not a top-level step). */
  statesById: Map<number, AgentThreadState>;
  /** Deepest canvas row below the lane — wave members, their bends and
   *  the fork columns all fold in here (0 = a flat one-row graph) — the
   *  view sizes the canvas height off it. */
  maxForkRows: number;
  /** The thread's top-level PLAN STEPS in plan order (the planning
   *  task — the ordinal-0 node 'plan' row — is NOT among them; it is
   *  planTask, the plan session's own row). */
  topLevelStates: AgentThreadState[];
  /** The run's planning task (begin_thread's row), or null on threads
   *  that predate it. */
  planTask: AgentThreadState | null;
  /** The store's runner-lane node states by node name (empty for
   *  threads that predate the store — statuses derived then). Chain
   *  segment k>0's runner names carry the "s<k>:" namespace — the
   *  canvas node ids join on them). */
  nodeStates: Map<string, AgentNodeState>;
  /** Step node items joined by the state row they executed. */
  nodeStatesByStateId: Map<number, AgentNodeState>;
  /** The LLM-context registry by context id — references and counts
   *  only (the messages are never persisted, by design). */
  contextsById: Map<string, AgentLlmContext>;
  /** "steps" — the plan-as-node-chain canvas; "legacy" — the fixed
   *  plan→execute lane (pre-step-graph threads and degraded runs). */
  mode: "steps" | "legacy";
  /** The chain's segments in order (single entry on legacy runs). */
  segments: ThreadSegmentView[];
}

/** True when the canvas node id is one of the chain's FINAL OUTPUT
 *  nodes (or the legacy result node) — the context panel reads their
 *  kind final-result / graph-output documents. */
export function isOutputNodeId(nodeId: string): boolean {
  return (
    nodeId === "result" ||
    nodeId === "output" ||
    /^s\d+:output$/.test(nodeId)
  );
}

/** The segment index a canvas node id belongs to (0 for bare ids and
 *  step/fork rows — state ids are globally unique; the s<k>: namespace
 *  marks chain segment k's runner-lane nodes). */
export function segmentIndexOfNodeId(nodeId: string): number {
  const m = nodeId.match(/^s(\d+):/);
  return m === null ? 0 : Number(m[1]);
}

/** One lane's node spacing (abstract units; ECharts auto-fits). */
const X_STEP = 210;

/** One vertical row's spacing — wave members stack in their shared
 *  column, fork spurs hang in their fork column; the fork-row
 *  arithmetic reads a parent's row back as y / Y_STEP, so both layouts
 *  must share the unit. */
const Y_STEP = 100;

/** The fork column's offset to the RIGHT of its parent step's column:
 *  every spawned subagent branch draws one X_FORK after (and below)
 *  its parent. Sized so a fork circle plus its right-side goal label
 *  (radius 15 + distance 7 + width 130 = 152) clears the next lane
 *  column: 160 + 152 = 312 < X_STEP_AFTER_FORKS − the next node's
 *  radius (340 − 22 = 318). */
const X_FORK = 160;

/** Extra spacing around a lane column that carries FORK SPURS — kept
 *  on BOTH sides (the column before and the one after advance past
 *  it): the spurs' right-side goal labels (reaching parent.x + X_FORK
 *  + 152 = parent.x + 312) need the room before the next lane node
 *  (parent.x + 340 − 22 = parent.x + 318), and the wave fork/merge
 *  bend legs drop at that next node's x, clear of the labels. */
const X_STEP_AFTER_FORKS = 340;

/** The tasks listed beneath the graph for a clicked node — the plan
 *  node's planning task, a step node's OWN row (its forks expand
 *  beneath it), a fork circle's own child row, legacy execute's whole
 *  task list; result and supervisor own no tasks (their views stay
 *  strictly their contexts — the supervisor's review document). */
export function itemsUnderNodeId(
  model: ThreadGraphModel,
  nodeId: string,
): AgentThreadState[] {
  if (nodeId === "plan") {
    return model.planTask !== null ? [model.planTask] : [];
  }
  if (nodeId === "supervisor" || isOutputNodeId(nodeId)) {
    // The output seats and the supervisor own no task rows — their
    // views stay strictly their contexts (the graph-output handoff
    // document / the final answer / the supervisor's review).
    return [];
  }
  if (nodeId.startsWith("fork-")) {
    const id = Number(nodeId.slice(5));
    const state = model.statesById.get(id);
    return state === undefined ? [] : [state];
  }
  if (model.mode === "legacy") {
    return nodeId === "execute" ? model.topLevelStates : [];
  }
  if (nodeId.startsWith("step-")) {
    const id = Number(nodeId.slice(5));
    const state = model.topLevelStates.find((s) => s.state_id === id);
    return state === undefined ? [] : [state];
  }
  return [];
}

/** The OPTIMISTIC planning-window canvas — what a generation in flight
 *  draws before its thread detail exists (the daemon registers the
 *  thread only once POST /run lands, and the planning rows persist a
 *  beat later): the run's THREE-GRAPH CHAIN as ghosts — plan
 *  (in_progress, "planning…") → handoff → custom (pending, the steps
 *  run here once the plan lands) → handoff → result (pending, the
 *  review wave + the final answer) → END — so the Generate click shows
 *  the chain shape immediately and the canvas never flashes when the
 *  real detail replaces this model. */
export function buildPendingThreadGraphModel(
  threadId: string,
): ThreadGraphModel {
  return {
    threadId,
    nodes: [
      { id: "start", kind: "endpoint", label: "START", status: "neutral",
        x: 0, y: 0 },
      { id: "plan", kind: "runner", label: "plan",
        sublabel: "planning…", status: "in_progress", x: X_STEP, y: 0 },
      { id: "output", kind: "output", label: "output",
        sublabel: "the plan", status: "in_progress",
        x: X_STEP * 2, y: 0 },
      { id: "s1:output", kind: "output", label: "output",
        sublabel: "findings synthesis", status: "pending",
        x: X_STEP * 4, y: 0 },
      { id: "s2:output", kind: "output", label: "output",
        sublabel: "the run's final answer", status: "pending",
        x: X_STEP * 6, y: 0 },
      { id: "end", kind: "endpoint", label: "END", status: "neutral",
        x: X_STEP * 7, y: 0 },
    ],
    edges: [
      { source: "start", target: "plan" },
      { source: "plan", target: "output" },
      { source: "output", target: "s1:output", kind: "handoff" },
      { source: "s1:output", target: "s2:output", kind: "handoff" },
      { source: "s2:output", target: "end" },
    ],
    active: true,
    superThreadId: threadId,
    childrenByParent: new Map<number, AgentThreadState[]>(),
    statesById: new Map<number, AgentThreadState>(),
    maxForkRows: 0,
    topLevelStates: [],
    planTask: null,
    nodeStates: new Map<string, AgentNodeState>(),
    nodeStatesByStateId: new Map<number, AgentNodeState>(),
    contextsById: new Map<string, AgentLlmContext>(),
    mode: "steps",
    segments: [],
  };
}

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
  // ONE NODE ONE CONTEXT, said on the canvas: the node's context id
  // (id tail only — the data itself is never persisted) + the calls
  // that ran under it.
  const ctxTail = (contextId: string | null): string =>
    contextId ? ` · ctx ${contextId.slice(0, 6)}` : "";

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
    if (segIdx === 0) edges.push({ source: "start", target: "plan" });
    nodes.push({
      id: "plan", kind: "runner", label: "plan",
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
      status: planStatus,
      x: X_STEP, y: 0,
    });
  }

  // The layout cursor: steps advance it by X_STEP, or X_STEP_AFTER_FORKS
  // when either flanking column carries fork spurs (their right-side
  // goal labels need the room on both sides — see X_STEP_AFTER_FORKS).
  // A segment without the plan circle (custom / result) opens on an
  // invisible ENTRY DOT at x=0 — the previous graph's HANDOFF edge
  // lands on it (and a wave opening the segment forks from it).
  let xCursor: number;
  let firstNodeId: string;
  if (drawPlan) {
    firstNodeId = "plan";
    xCursor = X_STEP;
  } else {
    firstNodeId = `s${segIdx}-in`;
    nodes.push({ id: firstNodeId, kind: "endpoint", label: "",
      status: "neutral", x: 0, y: 0 });
    xCursor = 0;
  }
  // Whether the lane column JUST placed carries fork spurs — the NEXT
  // column keeps X_STEP_AFTER_FORKS out of their labels too.
  let prevForks = false;

  // Deepest canvas row below the lane so far — wave members, their
  // bends and (in the fork-spur pass below) the fork columns all fold
  // in; the model exposes it as maxForkRows for the height budget.
  let maxRow = 0;

  // Pre-laid rows per FORK column x: a wave seeds the ENTRY node's
  // fork column (entryX + X_FORK) with its deepest member row, so a
  // forked step directly before a wave hangs its spurs below the
  // wave's crossing fork/merge legs instead of on them.
  const forkColumnSeeds = new Map<number, number>();

  // The lane cursor: the id of the last placed lane node, and a just-
  // laid-out wave's members awaiting their merge target — the wave's
  // left side forks from prev, its right side merges into the NEXT
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
    xCursor += X_STEP;
    const executeNs = nodeStates.get("execute");
    nodes.push({
      id: "execute", kind: "runner", label: "execute",
      sublabel: (executeNs !== undefined
        ? (executeNs.status === "running"
            ? `running · ${executeNs.n_llm_calls} llm · ${executeNs.n_tool_calls} tools`
            : executeNs.status === "failed" ? "agent loop failed"
            : `${executeNs.n_llm_calls} llm · ${executeNs.n_tool_calls} tools · ${executeNs.n_states} tasks`)
          + ctxTail(executeNs.llm_context_id)
        : active ? "agent loop running" : `${topLevel.length} steps walked`),
      status: runnerStatus("execute", active ? "in_progress" : "completed"),
      x: xCursor, y: 0,
    });
    edges.push({ source: prev, target: "execute" });
    prev = "execute";
  } else {
    // THE PLAN AS THE NODE CHAIN: every top-level state (including
    // removed/skipped ones — the chain is the audit) in ordinal order.
    // One step's canvas circle. A DELEGATED step ran no main
    // conversation — its circle shows the counts of the forks
    // (children) that did the work, read from their LLM-context
    // registry items.
    const pushStepNode = (
      state: AgentThreadState, x: number, y: number,
    ): string => {
      const id = `step-${state.state_id}`;
      const ns = nodeStatesByStateId.get(state.state_id);
      const forkIds = (childrenByParent.get(state.state_id) ?? []).map(
        (c) => c.llm_context_id,
      );
      let nLlm = ns?.n_llm_calls ?? 0;
      let nTools = ns?.n_tool_calls ?? 0;
      if (ns !== undefined && ns.llm_context_id === null) {
        for (const cid of forkIds) {
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
        sublabel: `${nLlm} llm · ${nTools} tools`
          + ctxTail(state.llm_context_id ?? ns?.llm_context_id ?? null),
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
    // The rows a state's fork subtree occupies in its fork column —
    // one per fork plus each fork's own subtree (the fork-spur pass
    // packs exactly this depth-first). A wave member advances the next
    // member past this, spacing forked parents apart vertically.
    const subtreeRows = (stateId: number): number => {
      let rows = 0;
      for (const child of childrenByParent.get(stateId) ?? []) {
        rows += 1 + subtreeRows(child.state_id);
      }
      return rows;
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
      const anyForks = run.some(
        (s) => (childrenByParent.get(s.state_id) ?? []).length > 0,
      );
      xCursor += anyForks || prevForks ? X_STEP_AFTER_FORKS : X_STEP;
      prevForks = anyForks;
      if (run.length === 1) {
        laneNext(pushStepNode(run[0], xCursor, 0));
      } else {
        // THE PARALLEL WAVE COLUMN: the members share one x and stack
        // vertically — same-size step circles running concurrently.
        // Tight one-apart while nothing forks; a member that carries
        // fork spurs pushes the NEXT member below its whole subtree,
        // so the spurs interleave between member rows — never ON one
        // (a merge leg crosses the fork column at every member row).
        // The fork legs are RIGHT ANGLES: the row-0 member hangs on
        // the lane (plain chain edge); every deeper member's fork
        // drops at the entry's x and turns right at ITS row through
        // an invisible bend node — a right triangle with the lane.
        // The merge legs mirror this at the next lane node
        // (mergeInto). No member-to-member chain — they ran side by
        // side, not in sequence.
        const entryNode = nodes.find((n) => n.id === prev);
        if (entryNode !== undefined) entryNode.labelTop = true;
        const entryX = entryNode?.x ?? 0;
        const memberRows: number[] = [];
        let nextRow = 0;
        for (const state of run) {
          memberRows.push(nextRow);
          nextRow += 1 + subtreeRows(state.state_id);
        }
        const memberIds = run.map(
          (state, k) => pushStepNode(state, xCursor, memberRows[k] * Y_STEP),
        );
        maxRow = Math.max(maxRow, memberRows[memberRows.length - 1]);
        memberIds.forEach((id, k) => {
          if (k === 0) {
            edges.push({ source: prev, target: id });
            return;
          }
          const bendId = `bend-fork-${run[k].state_id}`;
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
        // A forked step directly before this wave hangs its spurs in
        // the column one X_FORK right of the entry — below the wave's
        // deepest member row, off the crossing legs.
        const entryForkX = entryX + X_FORK;
        forkColumnSeeds.set(
          entryForkX,
          Math.max(
            forkColumnSeeds.get(entryForkX) ?? -1,
            memberRows[memberRows.length - 1],
          ),
        );
      }
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
  const resultX = xCursor + X_STEP;
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
            : "sealing the plan…")
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
      status: tStatus,
      x: resultX, y: 0,
    });
  } else {
    terminalId = "result";
    nodes.push({
      id: "result", kind: "runner", label: "result",
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
    endX = resultX + X_STEP;
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

  // ---- FORK SPURS (steps mode): every spawned subagent branch (and
  // anything the subagent itself added under its fork) draws in a FORK
  // COLUMN one X_FORK to the RIGHT of its parent step — each fork
  // circle horizontally after and vertically below its parent: a solid
  // spawn edge down-right, a dashed MERGE-BACK edge up-left (the
  // parent's conversation gathered the fork's result; the gathered
  // answers also surface in the clicked parent's context section).
  // Nested branches continue DOWNWARD in the same fork column through
  // a per-column row cursor, depth-first: each next fork lands below
  // everything placed so far in the column (a subtree ends above the
  // next sibling fork) and at least one row below its own parent.
  const statesById = new Map<number, AgentThreadState>();
  for (const s of states) statesById.set(s.state_id, s);
  if (mode === "steps") {
    const columnRow = new Map<number, number>(forkColumnSeeds);
    const addForks = (
      parentNode: ThreadGraphNode, parentStateId: number, columnX: number,
    ): void => {
      for (const child of childrenByParent.get(parentStateId) ?? []) {
        const row = Math.max(
          (columnRow.get(columnX) ?? -1) + 1,
          Math.round(parentNode.y / Y_STEP) + 1,
        );
        columnRow.set(columnX, row);
        maxRow = Math.max(maxRow, row);
        const forkNode: ThreadGraphNode = {
          id: `fork-${child.state_id}`,
          kind: "fork",
          label: "fork",
          // The fork's goal (the child row's title) — the canvas
          // identity of what the parent delegated/gathered.
          sublabel: child.title,
          status: child.status,
          x: columnX,
          y: row * Y_STEP,
        };
        nodes.push(forkNode);
        edges.push({ source: parentNode.id, target: forkNode.id, kind: "spawn" });
        edges.push({ source: forkNode.id, target: parentNode.id, kind: "merge" });
        addForks(forkNode, child.state_id, columnX);
      }
    };
    for (const state of topLevel) {
      const node = nodes.find((n) => n.id === `step-${state.state_id}`);
      if (node !== undefined) addForks(node, state.state_id, node.x + X_FORK);
    }
  }

  return {
    threadId: states[0]?.thread_id ?? "",
    nodes,
    edges,
    active,
    childrenByParent,
    statesById,
    maxForkRows: maxRow,
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

/** The inter-graph gap of the chain layout — one handoff edge's length
 *  plus breathing room between the previous segment's terminal and the
 *  next segment's entry. */
const X_CHAIN_GAP = 170;

/** The chain-level flags shared by every segment build. */
function chainFlags(detail: AgentThreadDetail): {
  active: boolean;
  hasFinalResult: boolean;
  hasGraphOutput: boolean;
} {
  return {
    active:
      detail.states.some(
        (s) => s.status === "pending" || s.status === "in_progress",
      ) || detail.node_states.some((s) => s.status === "running"),
    hasFinalResult: detail.contexts.some((c) => c.kind === "final-result"),
    hasGraphOutput: detail.contexts.some((c) => c.kind === "graph-output"),
  };
}

export function buildThreadGraphModel(
  detail: AgentThreadDetail,
): ThreadGraphModel {
  const segMetas =
    detail.segments !== undefined && detail.segments.length > 0
      ? detail.segments
      : [
          {
            thread_id: detail.super_thread_id ?? detail.thread_id,
            graph_type: "single",
          } as const,
        ];
  const flags = chainFlags(detail);
  const root = detail.super_thread_id ?? detail.thread_id;

  if (segMetas.length === 1) {
    // Legacy / degraded one-thread runs (and any single-segment chain —
    // a run stopped inside its plan phase): the exact pre-chain canvas.
    const m = buildSegmentModel({
      index: 0,
      last: true,
      graphType: segMetas[0].graph_type,
      states: detail.states,
      nodeStatesRows: detail.node_states,
      contexts: detail.contexts,
      llmContexts: detail.llm_contexts,
      ...flags,
    });
    return {
      ...m,
      threadId: detail.thread_id,
      superThreadId: root,
      segments: [
        {
          threadId: segMetas[0].thread_id,
          graphType: segMetas[0].graph_type,
          x0: m.minX,
          x1: m.maxX,
        },
      ],
    };
  }

  // ---- THE CHAIN: split the merged detail per segment (states by
  // their thread_id; the store's node items by their s<k>: namespace,
  // falling back to the owning state's segment), build each segment's
  // lane locally, then lay them LEFT TO RIGHT with the HANDOFF edge
  // between consecutive terminals and entries.
  const segStates = segMetas.map((meta) =>
    detail.states.filter((s) => s.thread_id === meta.thread_id),
  );
  const stateOwner = new Map<number, number>();
  segStates.forEach((rows, k) =>
    rows.forEach((s) => stateOwner.set(s.state_id, k)),
  );
  const segNodeRows: AgentNodeState[][] = segMetas.map(() => []);
  for (const ns of detail.node_states) {
    const m = ns.node.match(/^s(\d+):/);
    const k =
      m !== null
        ? Number(m[1])
        : ns.state_id !== null
          ? stateOwner.get(ns.state_id) ?? 0
          : 0;
    (segNodeRows[k] ?? segNodeRows[0]).push(ns);
  }
  const models = segMetas.map((meta, k) =>
    buildSegmentModel({
      index: k,
      last: k === segMetas.length - 1,
      graphType: meta.graph_type,
      states: segStates[k],
      nodeStatesRows: segNodeRows[k],
      contexts: detail.contexts,
      llmContexts: detail.llm_contexts,
      ...flags,
    }),
  );

  const nodes: ThreadGraphNode[] = [];
  const edges: ThreadGraphEdge[] = [];
  const views: ThreadSegmentView[] = [];
  const childrenByParent = new Map<number, AgentThreadState[]>();
  const statesById = new Map<number, AgentThreadState>();
  const nodeStates = new Map<string, AgentNodeState>();
  const nodeStatesByStateId = new Map<number, AgentNodeState>();
  const contextsById = new Map<string, AgentLlmContext>();
  let maxForkRows = 0;
  let anySteps = false;
  const topLevelStates: AgentThreadState[] = [];
  let shift = 0;
  models.forEach((m, k) => {
    for (const n of m.nodes) {
      n.x += shift;
      nodes.push(n);
    }
    edges.push(...m.edges);
    m.childrenByParent.forEach((v, key) => childrenByParent.set(key, v));
    m.statesById.forEach((v, key) => statesById.set(key, v));
    m.nodeStates.forEach((v, key) => nodeStates.set(key, v));
    m.nodeStatesByStateId.forEach((v, key) => nodeStatesByStateId.set(key, v));
    m.contextsById.forEach((v, key) => contextsById.set(key, v));
    maxForkRows = Math.max(maxForkRows, m.maxForkRows);
    anySteps = anySteps || m.mode === "steps";
    topLevelStates.push(...m.topLevelStates);
    views.push({
      threadId: segMetas[k].thread_id,
      graphType: segMetas[k].graph_type,
      x0: shift,
      x1: shift + m.maxX,
    });
    // The segment's floating CAPTION above its lane — the graph's type
    // (plan / custom / result), the chain's reading aid.
    nodes.push({
      id: `caption-${k}`, kind: "caption",
      label: segMetas[k].graph_type, status: "neutral",
      x: shift + m.maxX / 2, y: -3.2 * Y_STEP,
    });
    if (k > 0) {
      // THE SPECIAL INTER-GRAPH EDGE — one graph's FINAL OUTPUT to the
      // next graph's entry: the two graphs read as two graphs joined by
      // the handoff, never one lane of regular nodes.
      edges.push({
        source: models[k - 1].terminalId,
        target: m.firstNodeId,
        kind: "handoff",
      });
    }
    shift += m.maxX + X_CHAIN_GAP;
  });

  return {
    threadId: detail.thread_id,
    superThreadId: root,
    nodes,
    edges,
    active: flags.active,
    childrenByParent,
    statesById,
    maxForkRows,
    topLevelStates,
    planTask: models[0].planTask,
    nodeStates,
    nodeStatesByStateId,
    contextsById,
    mode: anySteps ? "steps" : "legacy",
    segments: views,
  };
}
