// Test-only executor: real sandbox/process tree and relay, synthetic DSH events, not native DSH.
import { spawn } from 'node:child_process';
import { Buffer } from 'node:buffer';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import { join, dirname } from 'node:path';

const work = process.env.BENCH_DSH_POLICY_INPUT ? dirname(dirname(process.env.BENCH_DSH_POLICY_INPUT)) : undefined;
const range = JSON.parse(readFileSync(work ? join(work, 'input/REVIEW_REQUEST.json') : '/input/REVIEW_REQUEST.json', 'utf8'));
const sessionId = randomUUID();
const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)', 'dsh-controlled-descendant'],
  { detached: true, stdio: 'ignore' });
child.unref();
console.log(JSON.stringify({ type: 'session', sessionId }));
const payload = JSON.stringify({ model: 'dsh-review', stream: true,
  messages: [{ role: 'user', content: JSON.stringify(range) }] });
const data = await new Promise((resolve, reject) => {
  const request = http.request({ socketPath: process.env.BENCH_DSH_SOCKET ?? '/model.sock', path: '/v1/chat/completions', method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } }, (reply) => {
    let body = '';
    reply.on('data', (chunk) => { body += chunk; });
    reply.on('end', () => resolve(body));
  });
  request.on('error', reject);
  request.end(payload);
});
const chunks = data.split('\n').filter((line) => line.startsWith('data: {'));
const response = JSON.parse(chunks.at(-1).slice(6));
console.log(JSON.stringify({ type: 'final', text: response.choices[0].delta.content }));
console.log(JSON.stringify({ type: 'status', phase: 'turn_end', reason: { kind: 'completed' } }));
