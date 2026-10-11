/**
 * filePathTree — turn the flat SecStaticsFile[] inventory into the nested
 * directory tree the Security Classification page renders as an expandable
 * table: one dir row per real directory under database/sec_statics/ (e.g.
 * "sec_classification/indices/CONS"), file rows as leaves, insertion order
 * preserved so the API's rules → owners → meta → catalog → indices sort
 * becomes the tree's natural top-to-bottom order.
 *
 * Dir rows carry subtree aggregates (file count / total bytes / latest
 * mtime / uniform generated flag) so the table can show them without
 * re-walking; filterTree prunes to the subtree of files whose path matches
 * a search string, dropping dirs with no match.
 */
import type { SecStaticsFile } from "@/lib/api-client/config";

export interface SecStaticsDirNode {
  kind: "dir";
  /** Last path segment ("CONS"). */
  name: string;
  /** Tree-relative dir path ("sec_classification/indices/CONS"). */
  path: string;
  depth: number;
  children: SecStaticsDirNode[];
  /** Direct descendant files, tree order. */
  files: SecStaticsFileNode[];
  /** Subtree file count (files included, not just direct). */
  fileCount: number;
  sizeBytes: number;
  /** Subtree max mtime. */
  updatedAt: string;
  /** true = every subtree file is generated, false = none is,
   *  null = mixed (no flag chip). */
  generated: boolean | null;
}

export interface SecStaticsFileNode {
  kind: "file";
  /** File name with extension ("AGRI.json"). */
  name: string;
  /** Tree-relative file path — the read/write key. */
  path: string;
  depth: number;
  file: SecStaticsFile;
}

export type SecStaticsTreeNode = SecStaticsDirNode | SecStaticsFileNode;

export function buildFilePathTree(files: SecStaticsFile[]): SecStaticsTreeNode[] {
  const dirs = new Map<string, SecStaticsDirNode>();
  const root: SecStaticsTreeNode[] = [];

  const dirNode = (dirPath: string): SecStaticsDirNode => {
    const existing = dirs.get(dirPath);
    if (existing != null) return existing;
    const slash = dirPath.lastIndexOf("/");
    const parentPath = slash === -1 ? null : dirPath.slice(0, slash);
    const node: SecStaticsDirNode = {
      kind: "dir",
      name: slash === -1 ? dirPath : dirPath.slice(slash + 1),
      path: dirPath,
      depth: dirPath.split("/").length - 1,
      children: [],
      files: [],
      fileCount: 0,
      sizeBytes: 0,
      updatedAt: "",
      generated: null,
    };
    dirs.set(dirPath, node);
    if (parentPath == null) root.push(node);
    else dirNode(parentPath).children.push(node);
    return node;
  };

  for (const file of files) {
    const slash = file.path.lastIndexOf("/");
    const parent = slash === -1 ? null : dirNode(file.path.slice(0, slash));
    const node: SecStaticsFileNode = {
      kind: "file",
      name: slash === -1 ? file.path : file.path.slice(slash + 1),
      path: file.path,
      depth: slash === -1 ? 0 : file.path.split("/").length - 1,
      file,
    };
    if (parent == null) root.push(node);
    else parent.files.push(node);
  }

  const aggregateDir = (
    node: SecStaticsDirNode,
  ): { count: number; size: number; at: string; gen: boolean | null } => {
    let count = 0;
    let size = 0;
    let at = "";
    let gen: boolean | null = null;
    const mergeGen = (fileGen: boolean) => {
      gen = gen == null ? fileGen : gen === fileGen ? gen : null;
    };
    for (const f of node.files) {
      count += 1;
      size += f.file.sizeBytes;
      if (f.file.updatedAt > at) at = f.file.updatedAt;
      mergeGen(f.file.generated);
    }
    for (const child of node.children) {
      const sub = aggregateDir(child);
      count += sub.count;
      size += sub.size;
      if (sub.at > at) at = sub.at;
      if (sub.gen != null) mergeGen(sub.gen);
    }
    node.fileCount = count;
    node.sizeBytes = size;
    node.updatedAt = at;
    node.generated = gen;
    return { count, size, at, gen };
  };
  for (const node of root) {
    if (node.kind === "dir") aggregateDir(node);
  }

  return root;
}

/** Prune to dirs containing at least one file whose path matches `query`
 *  (case-insensitive substring); surviving dirs keep their whole subtree
 *  structure but only matching files as leaves. */
export function filterFilePathTree(
  nodes: SecStaticsTreeNode[],
  query: string,
): SecStaticsTreeNode[] {
  const q = query.trim().toLowerCase();
  if (q === "") return nodes;
  const matches = (filePath: string) => filePath.toLowerCase().includes(q);
  const walkDir = (node: SecStaticsDirNode): SecStaticsDirNode | null => {
    const children = node.children
      .map(walkDir)
      .filter((child): child is SecStaticsDirNode => child != null);
    const files = node.files.filter((f) => matches(f.path));
    if (children.length === 0 && files.length === 0) return null;
    return { ...node, children, files };
  };
  const kept: SecStaticsTreeNode[] = [];
  for (const node of nodes) {
    if (node.kind === "file") {
      if (matches(node.path)) kept.push(node);
    } else {
      const pruned = walkDir(node);
      if (pruned != null) kept.push(pruned);
    }
  }
  return kept;
}
