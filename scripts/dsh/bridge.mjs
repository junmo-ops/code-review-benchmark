import http from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';

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
server.unref();
