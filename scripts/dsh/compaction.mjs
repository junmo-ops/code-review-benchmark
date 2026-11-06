import { pathToFileURL } from 'node:url';

const { default: BasicCompaction } = await import(pathToFileURL(
  (process.env.BENCH_DSH_RUNTIME ?? '/runtime') + '/node_modules/@deepseek-ai/dsh-compaction-basic/lib/index.js'));

// The shipped engine catches pressure-compaction errors and continues the turn.
// Cancel synchronously before that catch can permit another model request.
export default class FailClosedCompaction extends BasicCompaction {
  async compactIfNeeded(agent, trigger, signal) {
    try {
      return await super.compactIfNeeded(agent, trigger, signal);
    } catch (error) {
      agent.cancel({ kind: 'user' });
      throw error;
    }
  }
}
