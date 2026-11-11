import { Alert } from 'antd';
import type { Run } from '../types';

export function EvaluationSummary({ run }: { run: Run }) {
  const data = run.evaluation;
  if (!data) return null;
  const labels: Record<string, string> = { quality: '检视质量', stability: '运行稳定性' };
  return <Alert className="section-gap" type="info"
    message={`测评目的：${labels[data.mode] ?? '历史批次未记录'}；首次完整答卷 ${data.first_succeeded}/${data.first_observed}`}
    description={<>
      <div>首次结果覆盖 {data.first_observed}/{data.total}；后续执行/恢复查询 {data.platform_followups} 次。</div>
      <div>首次已记录耗时 {data.first_elapsed_seconds ?? '未知'} 秒；首次已知 Token {data.first_known_tokens ?? '未知'}。</div>
      <div>累计已记录耗时 {data.elapsed_seconds ?? '未知'} 秒；已知 Token {data.known_tokens ?? '未知'}（{data.token_attempts}/{data.attempts} 次有记录）。</div>
      <div>已识别平台故障 {data.platform_failures} 次；未确认归因失败 {data.unclassified_failures} 次。缺失数据和未结束执行不按零统计；未确认增量的续跑 Token 不累加。补跑不改写首次结果。</div>
    </>} />;
}
