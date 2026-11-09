import { Alert, Button, Modal, Space, Spin, Table, Typography } from 'antd';
import { useState } from 'react';
import { useRemote } from '../hooks/useRemote';
import { pct } from '../presentation';
import { ErrorAlert } from './Common';
import type { Run } from '../types';
interface Item {
  run: Run;
  usage: { seconds: number | null; tokens: number | null; seconds_coverage: number; tokens_coverage: number };
}
interface Reply { items: Item[]; warnings: string[] }
const number = (value: number | null) => value === null ? '未记录' : value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
const metrics = [
  { key: 'model', label: '模型', value: ({ run }: Item) => run.model },
  { key: 'reviewer', label: 'Agent / Skill', value: ({ run }: Item) => [run.reviewer, run.skill_name, run.skill_version].filter(Boolean).join(' · ') },
  { key: 'suite', label: '案例协议 / 评分器', value: ({ run }: Item) => `${run.suite} / ${run.evaluator}` },
  { key: 'judge', label: '裁判模型', value: ({ run }: Item) => run.judge_model ?? '未记录' },
  { key: 'mode', label: '测评目的', value: ({ run }: Item) => ({ quality: '检视质量', stability: '运行稳定性' } as Record<string, string>)[run.evaluation?.mode ?? ''] ?? '历史未记录' },
  { key: 'first', label: '首次完整答卷率', value: ({ run }: Item) => pct(run.evaluation?.first_success_rate ?? undefined) },
  { key: 'observed', label: '首次结果覆盖', value: ({ run }: Item) => run.evaluation ? `${run.evaluation.first_observed}/${run.total}` : '未知' },
  { key: 'followups', label: '后续执行 / 恢复查询', value: ({ run }: Item) => run.evaluation ? String(run.evaluation.platform_followups) : '未知' },
  { key: 'complete', label: '完整答卷 / 计划案例', value: ({ run }: Item) => `${run.completed}/${run.total}（${pct(run.total ? run.completed / run.total : undefined)}）` },
  { key: 'scored', label: '纳入评分', value: ({ run }: Item) => run.scored_cases == null ? '未确认' : `${run.scored_cases} 例` },
  { key: 'p', label: '精确率', value: ({ run }: Item) => pct(run.metrics?.precision) },
  { key: 'r', label: '召回率', value: ({ run }: Item) => pct(run.metrics?.recall) },
  { key: 'f1', label: 'F1', value: ({ run }: Item) => pct(run.metrics?.f1) },
  { key: 'state', label: '状态准确率（MCR）', value: ({ run }: Item) => pct(run.metrics?.state_accuracy) },
  { key: 'transition', label: '状态转移准确率（MCR）', value: ({ run }: Item) => pct(run.metrics?.transition_accuracy) },
  { key: 'tests', label: '测试通过率（c-CRAB）', value: ({ run }: Item) => pct(run.metrics?.test_pass_rate) },
  { key: 'time', label: '累计检视耗时（秒）', value: ({ usage, run }: Item) => `${number(usage.seconds)} · 覆盖 ${usage.seconds_coverage}/${run.total} 例` },
  { key: 'tokens', label: '检视 Token（已知合计）', value: ({ usage, run }: Item) => `${number(usage.tokens)} · 覆盖 ${usage.tokens_coverage}/${run.total} 例` },
];
export function RunComparison({ keys, close }: { keys: string[]; close: () => void }) {
  const [revision, setRevision] = useState(0);
  const params = new URLSearchParams(keys.map((key) => ['key', key]));
  params.set('revision', String(revision));
  const result = useRemote<Reply>('/api/run-comparison?' + params.toString());
  return (
    <Modal open title="测评结果对比" width="95vw" onCancel={close} footer={<Space>
      <Button onClick={() => setRevision((value) => value + 1)}>刷新结果</Button>
      <Button onClick={close}>关闭</Button>
    </Space>}>
      <Space direction="vertical" className="full-width">
        <Typography.Paragraph>读取各批次现有评分，不重新检视或评分，不计算共同案例分数，也不自动排名。运行中的结果可能继续变化。</Typography.Paragraph>
        <ErrorAlert error={result.error} />
        {!result.data && !result.error && <Spin />}
        {result.data?.warnings.map((message) => <Alert key={message} type="warning" showIcon message={message} />)}
        {result.data && <Table pagination={false} rowKey="key" dataSource={metrics.filter((row) => relevant(row.key, result.data!.items))}
          scroll={{ x: 240 + result.data.items.length * 250 }}
          columns={[{ title: '对比项', dataIndex: 'label', fixed: 'left', width: 200 },
            ...result.data.items.map((item) => ({ title: item.run.display_name ?? item.run.name,
              key: item.run.key, width: 250, render: (_: unknown, row: typeof metrics[number]) => row.value(item) }))]} />}
        <Typography.Paragraph type="secondary">新模式按逐次执行记录汇总已知耗时和 Token；历史批次仍读取已保存答卷，可能不含失败和重跑成本。覆盖数表示有记录的案例数，不保证每次请求用量完整。未确认增量的续跑 Token 不累加，缺失值不按零计算。累计案例耗时不是批次总历时，不含裁判和翻译。首次完整答卷率剔除已识别平台故障和主动中断，未确认归因的失败不直接解释为 Agent 故障。</Typography.Paragraph>
      </Space>
    </Modal>
  );
}

function relevant(key: string, items: Item[]) {
  if (key === 'tests') return items.some(({ run }) => run.suite === 'ccrab');
  if (key === 'state' || key === 'transition') return items.some(({ run }) => run.suite === 'mcr');
  return true;
}
