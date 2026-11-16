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
 * its engine) — its circle shows the child's counts as the spawn's.
 *
 * PARALLEL WAVE COLUMN: consecutive DELEGATED-or-PARALLEL steps (the
 * runner's wave — its members run concurrently; the marks ride each
 * step's step-input payload, written at plan time) draw as ONE column
 * of SAME-SIZE step circles STACKED VERTICALLY, wired as a SPAWN/MERGE
 * diamond with RIGHT-ANGLE PATHS: the entry lane node (plan, or the
 * previous step) spawns into EVERY member and every member merges into
 * the NEXT lane node (the next step, or result) — no member-to-member
 * chain, they ran side by side, not in sequence. Each spawn/merge path
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
 * Members that spawn push the NEXT member down past their whole spawn
 * subtree (subtreeRows): the wave column stays tight when nothing
 * spawns, but SPAWNED PARENTS gain the vertical room so their spurs
 * interleave between one member's row and the next — an upper spawned
 * parent's sub-sub nodes always stay ABOVE the lower parent node (and
 * off every member row, where the merge legs cross the spawn column).
 *
 * SPAWN SPURS: every spawned subagent branch (and anything the subagent
 * itself added under its spawn) draws in a SPAWN COLUMN one measured offset to the
 * RIGHT of its parent — each spawn circle sits HORIZONTALLY AFTER and
 * VERTICALLY BELOW its parent, wired by a RIGHT-ANGLE two-way pair: a
 * solid spawn edge drops at the parent's x, turns right at the spawn's
 * row through an invisible BEND node and arrows into the spawn, and the
 * dashed MERGE-BACK edge retraces the same elbow up into the parent
 * (the parent's conversation gathered the spawn's result; the gathered
 * answers also surface in the clicked parent's context section) — the
 * pair shares one L-shaped two-way connector with an arrowhead at each
 * end, so a parent's spurs fan out of one vertical trunk like an
 * org chart. A parent whose spurs render flips its label ABOVE the
 * circle (the trunk would cross the below-label — the same flip a
 * wave entry/exit makes). Spawn circles are smaller, wear the child
 * state's status, and label with the spawn goal. NESTED branches
 * continue DOWNWARD and one spawn-to-spawn offset to the RIGHT of their
 * own parent spawn — a slightly-right column per depth level, not one
 * shared column — depth-first through a per-column row cursor: each
 * next spawn lands below everything placed so far in its column (so a
 * spawn subtree always ends above the next sibling spawn) and at least
 * one row below its own parent. Wave members share their step column,
 * so they share one spawn column too — the member-spacing rule above
 * interleaves their spurs between the member rows.
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
 * a step node's own row (expanding its spawned children and its
 * input/result payloads), the plan session's planning task, legacy
 * execute's whole task list.
 *
 * THE LAYOUT — BOTTOM-UP, FOOTPRINT-MEASURED (2026-11-10; replaces the
 * flat X_STEP/X_STEP_AFTER_SPAWNS/X_SPAWN step constants): ECharts fits
 * the node-POSITION bbox into the canvas uniformly while every circle
 * and label keeps its fixed px (nodeScaleRatio 0 pins the circles at
 * symbolSize px under roam zoom too) — so screen spacing = abstract
 * spacing × fit scale while the footprints never shrink, and any flat
 * constant collides once the chain outgrows the canvas. The layout
 * therefore measures, bottom-up:
 *
 *   1. the footprint registry (KIND_SYMBOL_SIZE / KIND_LABEL_PX — the
 *      option builder renders from the same tables),
 *   2. every spawn subtree's row span, post-order from the leaf spawns
 *      (subtreeRows' memo) — a wave member's row and a spawn column's
 *      cursor advance by these MEASURED spans,
 *   3. lane columns last: each column's x = the previous column's
 *      trailing EDGE (its half-footprint, extended past its spawn
 *      column's outer label edge when it carries spurs) + GAP_COL + the
 *      column's half-footprint — a parent column is placed AFTER the
 *      children that size it, and a wide spawn label physically pushes
 *      the next lane column.
 *
 * The view itself stays UNCOMPRESSED and anchored: the canvas height
 * TRACKS the content (graphViewHeight — contentHeight × FIT_FLOOR, so
 * the vertical fit ratio holds at the floor as subtrees expand), the
 * spawn subtrees FOLD by default (a parent click expands one level — a
 * folded parent's column reserves no room), and the view's zoom rises
 * — never sinks — to FIT_FLOOR whenever the fit lands below it, around
 * a FROZEN center: the main path renders properly spaced, expansions
 * squeeze nothing, and a chain wider than the canvas pans (roam) from
 * the chain's left edge. The graph's bottom boundary is DRAGGABLE to
 * trade room back. No flat spacing constant survives: widen a label
 * or a circle and the layout, the height budget and the floor all
 * follow.
 */
import type {
  AgentLlmContext,
  AgentNodeState,
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
 *  FINAL OUTPUT NODE — the chain's handoff producer; a TERMINAL FORK
 *  continuation (2026-11-16) wears the same kind — the fork rode the
 *  seat's recorded conversation, so it renders as the seat's own node
 *  type hanging off it), step (a planned
 *  research step that executed as its own node hop), spawn (a spawned
 *  subagent branch, drawn in a spawn column to the right of and below
 *  its parent), fork (2026-11-13: an INHERIT-context subagent branch —
 *  the child continued a deep copy of its parent's transcript; drawn
 *  exactly like a spawn circle but its edge renders THICKER than a
 *  spawn's, the inheritance cue), supervisor (the main path's checker —
 *  ONE independent circle ABOVE the lane over result, off the walked
 *  chain), caption (a chain segment's floating title, invisible circle
 *  + text label), abandoned (2026-11-13: the drift fork's ABANDONED
 *  pre-fork tail — the OLD chain demoted to a right-side edge path off
 *  its fork node, drawn grey/skipped with a dashed thin leg), or bend
 *  (an invisible right-angle corner of a bent path — a wave spawn/merge
 *  leg or a spawn spur's elbow). */
export type ThreadGraphNodeKind =
  | "endpoint"
  | "runner"
  | "output"
  | "step"
  | "spawn"
  | "fork"
  | "abandoned"
  | "resume"
  | "supervisor"
  | "caption"
  | "bend";

export interface ThreadGraphNode {
  /** Stable id — "start" | "plan" | "step-<state_id>" | "supervisor" |
   *  "output" (segment 0's / legacy-final) | "s<k>:output" (chain
   *  segment k's final output node — the service's namespaced store
   *  name) | "result" (segment 0's legacy lane; later legacy lanes
   *  "s<k>:result", like every segment k>0 runner id "s<k>:plan" /
   *  "s<k>:execute" — the ids must stay UNIQUE across the chain, the
   *  graph series dedupes data items by id) | "end", path corners
   *  "bend-spawn-<state_id>" / "bend-merge-<state_id>" (a wave's
   *  spawn/merge legs) and "bend-spur-<state_id>" (a spawn spur's
   *  elbow), chain segment titles "caption-<k>". */
  id: string;
  kind: ThreadGraphNodeKind;
  /** Primary label (the node tag). */
  label: string;
  /** Secondary line under the label — the step's title, or the node's
   *  context id and call counts (one node one context). */
  sublabel?: string;
  /** The node's consumption line for the tooltip — "elapsed … ·
   *  zhipu · 53.4k tok (… in · … out)" (blank when nothing ran; the
   *  provider type leads when the chain recorded one). */
  metrics?: string;
  status: ThreadNodeStatus;
  /** Layout position (abstract units; ECharts auto-fits the view). */
  x: number;
  y: number;
  /** Label ABOVE the circle instead of below — wave entry/exit nodes
   *  whose below-label a vertical spawn/merge leg would cross. */
  labelTop?: boolean;
}

export interface ThreadGraphEdge {
  source: string;
  target: string;
  /** chain — the walked main lane; handoff — the INTER-GRAPH edge of
   *  the three-graph chain (one graph's final output node → the next
   *  graph's entry; styled like a chain edge but DASHED, and spanning
   *  the wider X_CHAIN_GAP so the two graphs read as two graphs);
   *  handoff_lead — a bent handoff's first legs (handoff-styled but
   *  WITHOUT the target arrowhead — routed a row above the lane when a
   *  terminal fork continuation sits on the lane row between the
   *  terminal and the next entry, 2026-11-16);
   *  spawn — parent → spawn (the elbow's arrowed leg into the spawn);
   *  fork — parent → fork (the elbow's arrowed leg into a fork child,
   *  rendered THICKER than a spawn's — the inherit-context cue,
   *  2026-11-13; on the drifted topology the lane's parent → inline
   *  fork edge carries this kind too); fork_lead — the fork elbow's
   *  first leg (fork-styled but WITHOUT the target arrowhead — the
   *  corner is wiring); abandoned — fork → abandoned tail (the OLD
   *  chain demoted to the right-side edge path: dashed and thin, the
   *  opposite of the fork weight); merge — spawn → parent (the dashed
   *  merge-back leg retracing the elbow); bare — a chain-styled leg
   *  WITHOUT the target arrowhead (the first leg of a bent path — a
   *  wave spawn/merge leg or a spawn spur's elbow — the corner gets no
   *  arrow); resume — parent → resume marker (the SUPERVISOR RESUME
   *  fork action, 2026-11-15: dashed and fork-weighted, the retry
   *  continuation cue). Absent = chain. */
  kind?: "chain" | "handoff" | "handoff_lead" | "spawn" | "fork"
    | "fork_lead" | "abandoned" | "merge" | "bare" | "resume";
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

/** The canvas' content bounding box in abstract units over ALL node
 *  POSITIONS — the exact geometry the ECharts view auto-fits (it fits
 *  the positions bbox into the view rect; the fixed-px footprints hang
 *  outside by their halves). The view's height budgets
 *  (graphViewHeight / fitViewHeight) read this. */
export interface ThreadGraphContentBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
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
  /** DIRECT child count by parent CANVAS node id ("step-<id>" /
   *  "spawn-<id>") — the expand/fold registry: a key present = the node
   *  is foldable (it owns spawn spurs), the value = what expanding
   *  reveals. The smallest level (spawn subtrees) is FOLDED by default;
   *  the layout skips a folded parent's spawn column entirely (no
   *  reserved room, no spurs), so the main path stays properly spaced. */
  childCountByNodeId: Map<string, number>;
  /** Every state row by id — spawn spur lookups (a spawn is a child row,
   *  not a top-level step). */
  statesById: Map<number, AgentThreadState>;
  /** Deepest canvas row below the lane — wave members, their bends and
   *  the spawn columns all fold in here (0 = a flat one-row graph). */
  maxSpawnRows: number;
  /** The content bbox (see ThreadGraphContentBounds). */
  contentBounds: ThreadGraphContentBounds;
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
