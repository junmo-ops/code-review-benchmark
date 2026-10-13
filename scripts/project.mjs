/** Cross-platform workspace commands; all paths are independent of the caller's cwd. */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const windows = process.platform === "win32";
const python = join(
  root,
  ".venv",
  windows ? "Scripts/python.exe" : "bin/python",
);
const env = {
  ...process.env,
  PYTHONUTF8: "1",
  PYTHONUNBUFFERED: "1",
  BENCH_PROJECT_ROOT: root,
  PYTHONPATH: [join(root, "backend/src"), root, process.env.PYTHONPATH]
    .filter(Boolean)
    .join(windows ? ";" : ":"),
};
const children = new Set();
let stopping = false;
function invoke(command, args) {
  const result = spawnSync(command, args, {
    cwd: root,
    env,
    stdio: "inherit",
    shell: windows && command === "npm",
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`${command} 执行失败（${result.status}）`);
}
function requirePython() {
  if (!existsSync(python))
    throw new Error("未找到 .venv，请先运行 npm run setup");
}
function setup() {
  if (!existsSync(python)) {
    const command = process.env.BENCH_PYTHON || (windows ? "py" : "python3");
    invoke(command, ["-m", "venv", join(root, ".venv")]);
  }
  invoke(python, ["-m", "pip", "install", "-e", join(root, "backend")]);
  invoke("npm", ["--prefix", "frontend", "ci"]);
  console.log(
    "依赖准备完成；执行 npm run dev 启动。LCR 与仓库资源按 docs/README.md 准备。",
  );
}
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) {
    if (windows)
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
      });
    else {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch (error) {
        if (error.code !== "ESRCH") console.error(error.message);
      }
    }
  }
}
function launch(command, args, childEnv = env) {
  const child = spawn(command, args, {
    cwd: root,
    env: childEnv,
    stdio: "inherit",
    detached: !windows,
    shell: windows && command === "npm",
  });
  children.add(child);
  child.once("error", (error) => {
    console.error(error.message);
    stop(1);
  });
  child.once("exit", (code, signal) => {
    children.delete(child);
    if (!stopping) stop(signal ? 1 : code || 0);
  });
  return child;
}
function servicePort(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65535)
    throw new Error(`${name} 端口无效`);
  return value;
}
function start(development) {
  requirePython();
  if (!development && !existsSync(join(root, "frontend/dist/index.html")))
    throw new Error("前端尚未构建，请先运行 npm run build");
  const apiPort = servicePort("BENCH_API_PORT", 8765);
  const frontendPort = servicePort("BENCH_FRONTEND_PORT", development ? 5173 : 4173);
  const frontendEnv = {
    ...env,
    BENCH_API_UPSTREAM: `http://127.0.0.1:${apiPort}`,
  };
  if (development) {
    launch(python, [join(root, "backend/src/ui.py"), "--no-open", "--port", String(apiPort)]);
    launch("npm", ["--prefix", "frontend", "run", "dev", "--", "--port", String(frontendPort), "--strictPort"], frontendEnv);
    console.log(`开发模式：Vite ${frontendPort} → Python API ${apiPort}；Ctrl+C 同时停止两者。`);
    return;
  }
  launch(python, [join(root, "backend/src/ui.py"), "--no-open", "--port", String(frontendPort)]);
  console.log(`Python 工作台已启动：http://127.0.0.1:${frontendPort}`);
}
function check() {
  invoke(process.execPath, [join(root, "scripts/check-dependencies.mjs")]);
  console.log("检查 Python 语法与复杂度、前端和脚本规范、TypeScript 类型。");
  invoke(python, [join(root, "tools/check_project.py")]);
  invoke("npm", ["--prefix", "frontend", "run", "lint"]);
  invoke(process.execPath, [
    "frontend/node_modules/eslint/bin/eslint.js", "scripts",
    "--config", "scripts/eslint.config.mjs", "--max-warnings", "0",
  ]);
  invoke("npm", ["--prefix", "frontend", "run", "typecheck"]);
}
function verify() {
  check();
  invoke("npm", ["--prefix", "frontend", "run", "bundle"]);
  invoke("git", ["diff", "--check"]);
  invoke("git", ["diff", "--cached", "--check"]);
  console.log("本地校验通过；可以打包交付。未调用模型或外部 COS。");
}
function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === "setup") return setup();
  requirePython();
  if (command === "dev" || command === "start") return start(command === "dev");
  if (command === "backend")
    return launch(python, [
      join(root, "backend/src/ui.py"),
      "--no-open",
      ...args,
    ]);
  if (command === "cli")
    return launch(python, [join(root, "backend/src/local.py"), ...args]);
  if (command === "check") return check();
  if (command === "verify") return verify();
  if (command === "package") {
    return invoke(python, [join(root, "tools/package_changes.py"), ...args]);
  }
  throw new Error(
    "使用 npm run setup/dev/start/backend/frontend/build/check/verify/package:changes/cli",
  );
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => stop(0));
try {
  main();
} catch (error) {
  console.error(error.message);
  stop(1);
}
