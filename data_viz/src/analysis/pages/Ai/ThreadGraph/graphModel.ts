/**
 * graphModel — the LangGraph → display-graph mapping for the Thread
 * Graph view (pure, no React/ECharts imports so it stays unit-clear).
 *
 * The runner (llm_agents/deep_agents/graph/runner.py) compiles a StateGraph
 * whose NODE CHAIN IS THE PLAN: the plan node studies the business
 * input (scope, focus question, skills, analysis items) and writes the
 * research steps, each of which executes as its OWN graph node hop —
 * one superstep, one LLM conversation per step — routed hop by hop off
 * the LIVING plan tree (a step's update_plan adds/removes/revokes
 * not-yet-run steps and the next hop follows).
 *
 * CANVAS = TOPOLOGY ONLY (the LangGraph/LangSmith observability
 * contract): the canvas draws exactly the chain the runtime walked —
 *
 *     START → plan → <the thread's top-level steps, ordinal order> →
 *             result → END
 *
 * The runner's own finalize settle phase is NOT drawn — it is
 * bookkeeping (no LLM, no answer), so the chain runs the steps
 * straight into result and the settle's outcome reads off result's
 * status.
 *
 * with each step circle wearing its row's status and a sublabel of its
 * call counts (joined through the store's step node item by state_id —
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
 * FORK SPURS: every spawned subagent branch (and anything the subagent
 * itself added under its fork) draws as a VERTICAL spur under its
 * parent step — a solid spawn edge down, a dashed MERGE-BACK edge up
 * (the parent's conversation gathered the fork's result; the gathered
 * answers also surface in the clicked parent's context section). Fork
 * circles are smaller, wear the child state's status, and label to the
 * right with the fork goal; nested branches continue downward in the
 * same column, siblings stacking depth-first below the deepest branch
 * (wave members share their column, so their forks start below the
 * whole wave column).
 *
 * The PLANNING TASK: every run starts with a running planning node —
 * the ordinal-0 top-level state row (node 'plan', in_progress) the
 * daemon's begin_thread writes at run start, closed by the plan node
 * when the proposal lands. It is NOT a step circle (the model-planned
 * chain is the steps, not the runner's own phase): the plan runner
 * circle wears its status, and clicking it lists the planning task as
 * its own row beneath the graph. While that row is the thread's ONLY
 * state, the graph is the planning window — zero step circles between
 * plan and result.
 *
 * LEGACY lane: threads from before the step graph (and degraded runs
 * whose plan never persisted — those ran the one-conversation
 * "execute" lane) carry a node:execute item instead of node:step-*
 * items; for them the canvas keeps the fixed runner chain
 * START → plan → execute → result → END and the steps
 * render as execute's task list beneath the graph, as before. A thread
 * with NO store items is legacy only once settled — a live thread with
 * no items yet is a steps-mode thread inside its planning window (the
 * store item for the plan node lands within instants of the row).
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
 * input/result payloads), the plan node's planning task, legacy
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
 *  END), runner (the plan/finalize/result lane), step (a planned
 *  research step that executed as its own node hop), fork (a spawned
 *  subagent branch, drawn as a vertical spur under its parent), or
 *  bend (an invisible right-angle corner of a wave fork/merge path). */
export type ThreadGraphNodeKind =
  | "endpoint"
  | "runner"
  | "step"
  | "fork"
  | "bend";

export interface ThreadGraphNode {
  /** Stable id — "start" | "plan" | "step-<state_id>" | "result" |
   *  "end" (legacy lanes: "execute" instead of steps), wave path
   *  corners "bend-fork-<state_id>" / "bend-merge-<state_id>". */
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
  /** chain — the walked main lane; spawn — parent → fork (down);
   *  merge — fork → parent (the dashed merge-back edge); bare — a
   *  chain-styled leg WITHOUT the target arrowhead (the first leg of a
   *  bent wave path — the corner gets no arrow). Absent = chain. */
  kind?: "chain" | "spawn" | "merge" | "bare";
}

export interface ThreadGraphModel {
  threadId: string;
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
  /** Deepest fork row below the main lane (0 = no forks) — the view
   *  sizes the canvas height off it. */
  maxForkRows: number;
  /** The thread's top-level PLAN STEPS in plan order (the planning
   *  task — the ordinal-0 node 'plan' row — is NOT among them; it is
   *  planTask, the plan node's own row). */
  topLevelStates: AgentThreadState[];
  /** The run's planning task (begin_thread's row), or null on threads
   *  that predate it. */
  planTask: AgentThreadState | null;
  /** The store's runner-lane node states by node name (empty for
   *  threads that predate the store — statuses derived then). */
  nodeStates: Map<string, AgentNodeState>;
  /** Step node items joined by the state row they executed. */
  nodeStatesByStateId: Map<number, AgentNodeState>;
  /** The LLM-context registry by context id — references and counts
   *  only (the messages are never persisted, by design). */
  contextsById: Map<string, AgentLlmContext>;
  /** "steps" — the plan-as-node-chain canvas; "legacy" — the fixed
   *  plan→execute lane (pre-step-graph threads and degraded runs). */
  mode: "steps" | "legacy";
}

/** One lane's node spacing (abstract units; ECharts auto-fits). */
const X_STEP = 210;

/** One vertical row's spacing — wave members stack one Y_STEP apart in
 *  their shared column, fork spurs hang one Y_STEP under their parent;
 *  the fork-row arithmetic reads a parent's row back as y / Y_STEP, so
 *  both layouts must share the unit. */
const Y_STEP = 100;

/** Extra spacing after a column that carries FORK SPURS: the fork's
 *  right-side goal label (width ~130 + the small circle) has to fit
 *  between the column and the next one — fork circles of adjacent
 *  columns share the same y, so a plain X_STEP gap runs their labels
 *  into each other. */
const X_STEP_AFTER_FORKS = 340;

/** The tasks listed beneath the graph for a clicked node — the plan
 *  node's planning task, a step node's OWN row (its forks expand
 *  beneath it), a fork circle's own child row, legacy execute's whole
 *  task list; result owns no tasks (its view stays strictly its
 *  contexts). */
export function itemsUnderNodeId(
  model: ThreadGraphModel,
  nodeId: string,
): AgentThreadState[] {
  if (nodeId === "plan") {
    return model.planTask !== null ? [model.planTask] : [];
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

export function buildThreadGraphModel(
  detail: AgentThreadDetail,
): ThreadGraphModel {
  // Top-level = the model-planned steps; the planning task (the
  // ordinal-0 node 'plan' row every run begins with) is separated out —
  // it rides the plan runner node, never the step chain.
  const topLevel = detail.states
    .filter((s) => s.parent_state_id === null && s.node !== "plan")
    .sort((a, b) => a.ordinal - b.ordinal || a.state_id - b.state_id);
  const planTask =
    detail.states.find(
      (s) => s.parent_state_id === null && s.node === "plan",
    ) ?? null;

  const childrenByParent = new Map<number, AgentThreadState[]>();
  for (const s of detail.states) {
    if (s.parent_state_id !== null) {
      const siblings = childrenByParent.get(s.parent_state_id) ?? [];
      siblings.push(s);
      childrenByParent.set(s.parent_state_id, siblings);
    }
  }

  const nodeStates = new Map<string, AgentNodeState>();
  const nodeStatesByStateId = new Map<number, AgentNodeState>();
  for (const ns of detail.node_states) {
    nodeStates.set(ns.node, ns);
    if (ns.state_id !== null) nodeStatesByStateId.set(ns.state_id, ns);
  }
  const contextsById = new Map<string, AgentLlmContext>();
  for (const ctx of detail.llm_contexts) contextsById.set(ctx.context_id, ctx);
  // The step-input payloads by context id — the wave marks (the
  // planner's parallel assertion / delegation goal) live there.
  const inputPayloads = new Map<number, unknown>();
  for (const c of detail.contexts) inputPayloads.set(c.context_id, c.payload);

  const statesActive = detail.states.some(
    (s) => s.status === "pending" || s.status === "in_progress",
  );
  const nodeRunning = detail.node_states.some((s) => s.status === "running");
  const active = statesActive || nodeRunning;
  // The result node's fixed output landed (the kind final-result
  // context — the run's answer document). Absent while the run is live
  // before the result node, and forever on stopped / pre-feature
  // threads.
  const hasFinalResult = detail.contexts.some(
    (c) => c.kind === "final-result",
  );

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
      : detail.states.length > 0
        ? "completed"
        : "failed",
  );

  // The mode: step-chain threads carry node:step-* items; legacy (and
  // degraded — the plain execute lane) threads carry node:execute. A
  // thread with NO store items yet is a steps-mode thread inside its
  // planning window while live (its items land within instants of the
  // first row) and legacy only once settled (pre-store threads).
  const hasStepItems = detail.node_states.some((ns) =>
    ns.node.startsWith("step-"),
  );
  const hasExecuteItem = detail.node_states.some(
    (ns) => ns.node === "execute",
  );
  const mode: "steps" | "legacy" =
    hasStepItems ||
    (!hasExecuteItem && (detail.node_states.length > 0 || active))
      ? "steps"
      : "legacy";

  const nodes: ThreadGraphNode[] = [
    { id: "start", kind: "endpoint", label: "START", status: "neutral",
      x: 0, y: 0 },
    {
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
          : detail.states.length > 0 ? `${topLevel.length} steps planned` : "no plan",
      status: planStatus,
      x: X_STEP, y: 0,
    },
  ];
  const edges: ThreadGraphEdge[] = [
    { source: "start", target: "plan" },
  ];

  // The layout cursor: steps advance it by X_STEP, or X_STEP_AFTER_FORKS
  // when the column carries fork spurs (their right-side goal labels need
  // the room — see X_STEP_AFTER_FORKS).
  let xCursor = X_STEP;

  // Pre-occupied rows per column x — the wave members' column and the
  // entry/exit bend stacks: the fork-spur pass seeds its per-column
  // row cursor from these, so spurs start below everything a wave
  // already parked in that column.
  const waveColumnSeeds = new Map<number, number>();

  // The lane cursor: the id of the last placed lane node, and a just-
  // laid-out wave's members awaiting their merge target — the wave's
  // left side forks from prev, its right side merges into the NEXT
  // lane node (the next step, or result). The row-0 member merges on
  // the lane; deeper members merge through right-angle bends at the
  // target's x: each bent path is a vertical leg at the entry/exit
  // node's x plus a horizontal leg at the member's row — a right
  // triangle with the lane (the graph series draws straight segments,
  // so an invisible bend node is the corner; the entry/exit node's
  // label flips above to keep the vertical leg clear).
  let prev = "plan";
  let pendingMerges: { straightId: string; bentIds: string[] } | null = null;
  const mergeInto = (id: string): void => {
    if (pendingMerges === null) {
      edges.push({ source: prev, target: id });
      return;
    }
    edges.push({ source: pendingMerges.straightId, target: id });
    const exitNode = nodes.find((n) => n.id === id);
    if (exitNode !== undefined) exitNode.labelTop = true;
    const exitX = exitNode?.x ?? 0;
    pendingMerges.bentIds.forEach((src, k) => {
      const row = k + 1;
      const bendId = `bend-merge-${src.slice("step-".length)}`;
      nodes.push({
        id: bendId, kind: "bend", label: "", status: "neutral",
        x: exitX, y: row * Y_STEP,
      });
      edges.push({ source: src, target: bendId, kind: "bare" });
      edges.push({ source: bendId, target: id });
    });
    // The exit's own fork spurs start BELOW the merge bends.
    waveColumnSeeds.set(exitX, pendingMerges.bentIds.length);
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
      const forkMark = ns !== undefined && ns.llm_context_id === null
        ? (forkIds.length > 0 ? "fork · " : "")
        : "";
      nodes.push({
        id,
        kind: "step",
        label: `${state.ordinal}. ${state.node}`,
        sublabel: `${forkMark}${nLlm} llm · ${nTools} tools`
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
      xCursor += anyForks ? X_STEP_AFTER_FORKS : X_STEP;
      if (run.length === 1) {
        laneNext(pushStepNode(run[0], xCursor, 0));
      } else {
        // THE PARALLEL WAVE COLUMN: the members share one x and stack
        // one Y_STEP apart — same-size step circles running
        // concurrently. The fork legs are RIGHT ANGLES: the row-0
        // member hangs on the lane (plain chain edge); every deeper
        // member's fork drops at the entry's x and turns right at its
        // row through an invisible bend node — a right triangle with
        // the lane. The merge legs mirror this at the next lane node
        // (mergeInto). No member-to-member chain — they ran side by
        // side, not in sequence.
        const entryNode = nodes.find((n) => n.id === prev);
        if (entryNode !== undefined) entryNode.labelTop = true;
        const entryX = entryNode?.x ?? 0;
        const memberIds = run.map(
          (state, row) => pushStepNode(state, xCursor, row * Y_STEP),
        );
        memberIds.forEach((id, row) => {
          if (row === 0) {
            edges.push({ source: prev, target: id });
            return;
          }
          const bendId = `bend-fork-${run[row].state_id}`;
          nodes.push({
            id: bendId, kind: "bend", label: "", status: "neutral",
            x: entryX, y: row * Y_STEP,
          });
          edges.push({ source: prev, target: bendId, kind: "bare" });
          edges.push({ source: bendId, target: id });
        });
        pendingMerges = {
          straightId: memberIds[0],
          bentIds: memberIds.slice(1),
        };
        // The members and both bend stacks pre-occupy their rows, so
        // fork spurs start below them.
        waveColumnSeeds.set(xCursor, run.length - 1);
        waveColumnSeeds.set(entryX, run.length - 1);
      }
    }
  }

  // No finalize circle: the runner's settle phase is bookkeeping, not a
  // stop worth a node — the chain runs straight into result.
  const resultX = xCursor + X_STEP;
  nodes.push({
    id: "result", kind: "runner", label: "result",
    // The graph's LAST node — its fixed output (the kind final-result
    // context) is what the Result view displays.
    sublabel: hasFinalResult
      ? "final answer recorded"
      : active ? "waiting for the answer" : "no result recorded",
    status: runnerStatus(
      "result",
      hasFinalResult ? "completed" : active ? "pending" : "skipped"),
    x: resultX, y: 0,
  });
  nodes.push({ id: "end", kind: "endpoint", label: "END", status: "neutral",
    x: resultX + X_STEP, y: 0 });
  // A wave that ended the lane merges ALL its members into result
  // (right-angle bends at result's x); otherwise the last lane node
  // chains in.
  mergeInto("result");
  edges.push({ source: "result", target: "end" });

  // ---- FORK SPURS (steps mode): every spawned subagent branch (and
  // anything the subagent itself added under its fork) draws as a
  // VERTICAL spur under its parent step — a solid spawn edge down, a
  // dashed MERGE-BACK edge up (the parent's conversation gathered the
  // fork's result; the gathered answers also surface in the clicked
  // parent's context section). Nested branches continue downward in the
  // parent's column; siblings stack depth-first below the deepest
  // branch through a per-column row cursor, so no spur collides.
  const statesById = new Map<number, AgentThreadState>();
  for (const s of detail.states) statesById.set(s.state_id, s);
  let maxForkRows = 0;
  if (mode === "steps") {
    // Wave columns pre-occupy their rows 0..N-1 (the members), so a
    // member's forks stack below the whole column; the seeds also
    // fold the wave depth into the canvas-height budget.
    const columnRow = new Map<number, number>(waveColumnSeeds);
    for (const seeded of columnRow.values()) {
      maxForkRows = Math.max(maxForkRows, seeded);
    }
    const addForks = (
      parentNode: ThreadGraphNode, parentStateId: number,
    ): void => {
      for (const child of childrenByParent.get(parentStateId) ?? []) {
        const row = Math.max(
          (columnRow.get(parentNode.x) ?? 0) + 1,
          Math.round(parentNode.y / Y_STEP) + 1,
        );
        columnRow.set(parentNode.x, row);
        maxForkRows = Math.max(maxForkRows, row);
        const forkNode: ThreadGraphNode = {
          id: `fork-${child.state_id}`,
          kind: "fork",
          label: "fork",
          // The fork's goal (the child row's title) — the canvas
          // identity of what the parent delegated/gathered.
          sublabel: child.title,
          status: child.status,
          x: parentNode.x,
          y: row * Y_STEP,
        };
        nodes.push(forkNode);
        edges.push({ source: parentNode.id, target: forkNode.id, kind: "spawn" });
        edges.push({ source: forkNode.id, target: parentNode.id, kind: "merge" });
        addForks(forkNode, child.state_id);
      }
    };
    for (const state of topLevel) {
      const node = nodes.find((n) => n.id === `step-${state.state_id}`);
      if (node !== undefined) addForks(node, state.state_id);
    }
  }

  return {
    threadId: detail.thread_id,
    nodes,
    edges,
    active,
    childrenByParent,
    statesById,
    maxForkRows,
    topLevelStates: topLevel,
    planTask,
    nodeStates,
    nodeStatesByStateId,
    contextsById,
    mode,
  };
}
