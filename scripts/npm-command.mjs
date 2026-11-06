/** Run npm's CLI directly so Windows arguments never pass through cmd.exe. */
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';

export function npmCommand(args) {
  const cli = process.env.npm_execpath || join(dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js');
  if (!existsSync(cli)) throw new Error('未找到 npm CLI，请通过 npm run 执行项目命令。');
  return [process.execPath, [cli, ...args]];
}
