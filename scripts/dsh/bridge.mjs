import http from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// Only the host relay knows the frozen upstream address and credential.
const server = http.createServer((request, response) => {
  if (process.env.BENCH_DSH_SOCKET && request.headers.authorization !== 'Bearer ' + process.env.BENCH_DSH_KEY) {
    response.writeHead(403);
    response.end();
    return;
  }
  const upstream = http.request({ socketPath: process.env.BENCH_DSH_SOCKET ?? '/model.sock', method: request.method,
    path: request.url, headers: request.headers }, (reply) => {
    response.writeHead(reply.statusCode, reply.headers);
    reply.pipe(response);
  });
  upstream.on('error', () => { response.writeHead(502); response.end(); });
  request.on('aborted', () => upstream.destroy());
  response.on('close', () => upstream.destroy());
  request.pipe(upstream);
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(process.env.BENCH_DSH_SOCKET ? 0 : 8123, '127.0.0.1', resolve);
});
if (process.env.BENCH_DSH_SOCKET) {
  const policy = JSON.parse(readFileSync(process.env.BENCH_DSH_POLICY_INPUT, 'utf8'));
  const provider = policy.find((row) => row.id === 'llm-pi-ai').config.providers.benchmark;
  provider.baseURL = `http://127.0.0.1:${server.address().port}/v1`;
  writeFileSync(process.env.BENCH_DSH_POLICY_OUTPUT, JSON.stringify(policy), { mode: 0o600 });
}
// Model requests ride Node's global fetch, whose undici defaults (300 s
// headers/body timeouts) sit below the SDK-side stream allowance
// (llm-pi-ai streamIdleTimeoutMs, configured to 10 minutes). A case whose
// upstream takes longer than 300 s to send its first response byte is aborted
// with "Node.js fetch timed out waiting for response headers" (observed for
// browser-use and ComfyUI archived runs). Install a dispatcher whose bounds
// match the SDK allowance for every request this process makes; setGlobalDispatcher
// publishes to the same symbol Node's built-in fetch reads on this process.
// BENCH_DSH_HEADERS_TIMEOUT_MS lowers the bound for mechanism tests.
// This script lives outside the DSH runtime tree on the deployment host, so a
// bare import('undici') resolves only when a sibling node_modules provides the
// package (it does not under /opt/deployments/scripts). Resolve undici from the
// runtime tree first, exactly like compaction.mjs, and fall back to the bare
// specifier for dev trees that hoist undici at the project root.
const undiciUri = pathToFileURL(
  (process.env.BENCH_DSH_RUNTIME ?? '/runtime') + '/node_modules/undici/index.js').href;
try {
  const loaded = await import(undiciUri);
  const undici = loaded.default ?? loaded;
  const bound = Number(process.env.BENCH_DSH_HEADERS_TIMEOUT_MS ?? 600_000) || 600_000;
  const dispatcher = new undici.Agent({ headersTimeout: bound, bodyTimeout: bound });
  undici.setGlobalDispatcher(dispatcher);
} catch (error) {
  try {
    const { Agent, setGlobalDispatcher } = await import('undici');
    const bound = Number(process.env.BENCH_DSH_HEADERS_TIMEOUT_MS ?? 600_000) || 600_000;
    const dispatcher = new Agent({ headersTimeout: bound, bodyTimeout: bound });
    setGlobalDispatcher(dispatcher);
  } catch (fallbackError) {
    // A locked-down runtime may drop undici; requests then keep Node defaults.
    console.error('dsh: bridge: undici dispatcher unavailable (' + undiciUri + '): '
      + error.message + '; bare fallback: ' + fallbackError.message);
  }
}
server.unref();
