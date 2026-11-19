import { Buffer } from "node:buffer";
import { execFileSync } from "node:child_process";
import { lstatSync, readFileSync, writeFileSync } from "node:fs";

const diffOptions = [
  "diff", "--cached", "-z", "--diff-filter=M", "--no-renames",
  "--no-ext-diff", "--no-textconv",
];
const toLf = (content) => content.replaceAll("\r\n", "\n");

function git(args, options = {}) {
  return execFileSync("git", args, { maxBuffer: 64 * 1024 * 1024, ...options });
}

function fixLineEndings(original, staged) {
  const previous = original.toString("latin1");
  const content = toLf(staged.toString("latin1"));
  if (toLf(previous) === content) return original;

  const hasCrlf = previous.includes("\r\n");
  const hasLf = /(?<!\r)\n/.test(previous);
  if (hasCrlf === hasLf) return staged;
  return Buffer.from(hasCrlf ? content.replaceAll("\n", "\r\n") : content, "latin1");
}

function findRepairs() {
  const textPaths = new Set(git([...diffOptions, "--numstat"], { encoding: "utf8" })
    .split("\0").filter((entry) => /^\d+\t\d+\t/.test(entry))
    .map((entry) => entry.replace(/^\d+\t\d+\t/, "")));
  const entries = git([...diffOptions, "--raw", "--no-abbrev"], { encoding: "utf8" }).split("\0");
  const repairs = [];
  for (let i = 0; i + 1 < entries.length; i += 2) {
    const path = entries[i + 1];
    if (!textPaths.has(path)) continue;
    const [oldMode, mode, originalId, stagedId] = entries[i].slice(1).split(" ");
    if (!/^100(644|755)$/.test(oldMode) || !/^100(644|755)$/.test(mode) || originalId === stagedId) continue;
    const original = git(["cat-file", "blob", originalId]);
    const staged = git(["cat-file", "blob", stagedId]);
    const fixed = fixLineEndings(original, staged);
    if (fixed.equals(staged)) continue;
    const id = fixed === original
      ? originalId
      : git(["hash-object", "-w", "--stdin"], { input: fixed, encoding: "utf8" }).trim();
    repairs.push({ path, mode, id, staged, fixed });
  }
  return repairs;
}

function fixMatchingWorktree({ path, staged, fixed }) {
  try {
    const file = lstatSync(path, { throwIfNoEntry: false });
    if (file?.isFile() && file.nlink === 1 && readFileSync(path).equals(staged)) {
      writeFileSync(path, fixed);
    }
  } catch {
    console.warn(`已修复暂存区的换行，工作区文件未能同步写入：${path}`);
  }
}

const repairs = findRepairs();
if (repairs.length) {
  const input = repairs.map(({ path, mode, id }) => `${mode} ${id}\t${path}\0`).join("");
  git(["update-index", "-z", "--index-info"], { input });
  for (const repair of repairs) fixMatchingWorktree(repair);
  console.log(`已自动修复 ${repairs.length} 个暂存文件的换行。`);
}
