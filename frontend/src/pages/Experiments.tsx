import { Markdown } from '../components/Markdown';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { api, download, query } from '../api';
import { creationName, submitCreation } from '../creation';
import { useWorkbench } from '../context';
import { useAction } from '../hooks/useAction';
import { useRequestScope } from '../hooks/useRequestScope';
import { useRemote } from '../hooks/useRemote';
import { active, availableProfiles, canSubmit, caseLabel, pct } from '../presentation';
import { ErrorAlert, Heading, Status } from '../components/Common';
import type { Case, Comparison, Evidence, TextReply } from '../types';
interface ExperimentValues {
  name: string;
  display_name?: string;
  suite: string;
  dataset: string;
  models: string[];
  judge_profile: string;
  timeout_minutes: number;
}
export function Experiments({ results = false }: { results?: boolean }) {
  const { state, openExperiment, importBusy } = useWorkbench();
  const [creating, setCreating] = useState(false);
  return (
    <>
      <Heading
        title={results ? '结果对比' : '多模型实验'}
        description={
          results
            ? '在同一实验的共同评分案例上比较质量、耗时和已记录用量。'
            : '统一案例、裁判与预算，逐组执行多个模型。'
        }
        action={
          !results && (
            <Button
              type="primary"
              disabled={!canSubmit(state) || importBusy}
              onClick={() => setCreating(true)}
            >
              创建实验
            </Button>
          )
        }
      />
      <Alert
        className="section-gap"
        type="info"
        message={
          results
            ? '仅比较各模型共同完成评分的案例，不合并不同案例集总分。'
            : '运行会调用模型；单案例失败后继续，失败单独记录。'
        }
      />
      <Table
        rowKey="key"
        dataSource={state.comparisons}
        columns={[
          {
            title: '实验',
            key: 'name',
            render: (_: unknown, c: Comparison) => (
              <Button type="link" onClick={() => openExperiment(c.key, results)}>
                {c.display_name ?? c.name}
              </Button>
            ),
          },
          { title: '状态', dataIndex: 'status', render: (s: string) => <Status status={s} /> },
          {
            title: '评分完成',
            key: 'progress',
            render: (_: unknown, c: Comparison) =>
              `${c.report.groups.reduce((n, g) => n + g.scored, 0)} / ${c.report.case_count * c.report.groups.length}`,
          },
          {
            title: '共同案例',
            key: 'common',
            render: (_: unknown, c: Comparison) =>
              `${c.report.common_cases.length}/${c.report.case_count}`,
          },
        ]}
      />
      <CreateExperiment open={creating} close={() => setCreating(false)} />
    </>
  );
}
function CreateExperiment({ open, close }: { open: boolean; close: () => void }) {
  const { state, refresh, openExperiment, importBusy } = useWorkbench();
  const [form] = Form.useForm<ExperimentValues>(),
    values = Form.useWatch([], form) ?? {};
  const { busy, error, run, clear } = useAction();
  const ownRequest = useRequestScope(open);
  const suites = state.catalog.filter((s) =>
    ['aacr', 'swe_prbench_llm', 'codefuse_cr'].includes(s.id),
  );
  const profiles = availableProfiles(state).filter((p) => p.review_ready);
  const suite = suites.find((s) => s.id === values.suite),
    initialized = useRef(false);
  useEffect(() => {
    if (!open) {
      initialized.current = false;
      return;
    }
    if (initialized.current) return;
    initialized.current = true;
    form.resetFields();
    clear();
    form.setFieldsValue({
      name: creationName('models'),
      suite: suites[0]?.id,
      dataset: suites[0]?.datasets[0]?.id,
      models: [],
      judge_profile: profiles[0]?.id,
      timeout_minutes: 10,
    });
  }, [open, form, suites, profiles, clear]);
  const enabled = canSubmit(state) && !importBusy && profiles.length >= 2;
  const submit = (values: ExperimentValues) =>
    run(async () => {
      const current = ownRequest();
      const reply = await submitCreation('/api/comparisons', values, 'compare-' + values.name, 'comparison');
      await refresh();
      if (!current()) return;
      close();
      openExperiment(reply.key);
    });
  return (
    <Modal
      open={open}
      title="创建多模型实验"
      width={640}
      onCancel={close}
      confirmLoading={busy}
      okText="创建并运行"
      okButtonProps={{ disabled: !enabled }}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
        <Form.Item name="display_name" label="实验名称（选填）">
          <Input maxLength={80} />
        </Form.Item>
        <Form.Item name="name" label="实验编号">
          <Input readOnly />
        </Form.Item>
        <Form.Item name="suite" label="案例集" rules={[{ required: true }]}>
          <Select
            options={suites.map((s) => ({ value: s.id, label: s.name }))}
            onChange={(id) =>
              form.setFieldValue('dataset', suites.find((s) => s.id === id)?.datasets[0]?.id)
            }
          />
        </Form.Item>
        <Form.Item name="dataset" label="案例范围" rules={[{ required: true }]}>
          <Select
            options={suite?.datasets.map((d) => ({
              value: d.id,
              label: `${d.name ?? d.id} · ${d.count} 例`,
            }))}
          />
        </Form.Item>
        <Form.Item
          name="models"
          label="被测模型（选择 2–6 个）"
          rules={[{ type: 'array', required: true, min: 2, max: 6 }]}
        >
          <Checkbox.Group
            options={profiles.map((p) => ({
              value: p.id,
              label: `${p.label} · ${p.review_model}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="judge_profile" label="统一裁判" rules={[{ required: true }]}>
          <Select options={profiles.map((p) => ({ value: p.id, label: p.label }))} />
        </Form.Item>
        <Form.Item
          name="timeout_minutes"
          label="每案例检视总时限（分钟）"
          rules={[{ required: true }]}
        >
          <InputNumber min={1} max={120} className="full-width" />
        </Form.Item>
        <Alert
          type="info"
          message="裁判每案例最多 10 分钟、400 个意见对；各组使用相同条件。停止后不自动续跑。"
        />
        {!enabled && (
          <Alert type="warning" message="请先准备至少两个可用模型，并等待资源导入完成。" />
        )}
        <ErrorAlert error={error} />
      </Form>
    </Modal>
  );
}
function GroupTable({ comparison, results }: { comparison: Comparison; results: boolean }) {
  const { openRun } = useWorkbench();
  const columns = [
    {
      title: '模型',
      key: 'model',
      render: (_: unknown, g: Comparison['report']['groups'][number]) => (
        <>
          <Button type="link" onClick={() => openRun(g.key)}>
            {g.label}
          </Button>
          <div className="muted">{g.model}</div>
        </>
      ),
    },
    {
      title: '检视 / 评分',
      key: 'progress',
      render: (_: unknown, g: Comparison['report']['groups'][number]) =>
        `${g.completed} / ${g.scored}（共 ${comparison.report.case_count} 例）`,
    },
    { title: '超时', dataIndex: 'timeouts' },
  ];
  const quality = [
    {
      title: '共同案例 F1',
      key: 'f1',
      render: (_: unknown, g: Comparison['report']['groups'][number]) => pct(g.common_quality?.f1),
    },
    {
      title: 'P / R',
      key: 'quality',
      render: (_: unknown, g: Comparison['report']['groups'][number]) =>
        `${pct(g.common_quality?.precision)} / ${pct(g.common_quality?.recall)}`,
    },
    { title: '平均检视秒数', dataIndex: 'mean_seconds' },
    {
      title: '已记录 Token / 覆盖',
      key: 'tokens',
      render: (_: unknown, g: Comparison['report']['groups'][number]) =>
        `${g.review_tokens.total_tokens ?? 0} / ${g.token_coverage} 例`,
    },
  ];
  return (
    <Table
      rowKey="key"
      dataSource={comparison.report.groups}
      columns={results ? [...columns, ...quality] : columns}
      pagination={false}
      scroll={{ x: 950 }}
    />
  );
}
export function ExperimentDetail({
  experimentKey,
  results,
  group,
  caseId,
  showEvidence,
  closeEvidence,
  close,
}: {
  experimentKey: string;
  results: boolean;
  group?: string;
  caseId?: string;
  showEvidence: (group: string, caseId: string) => void;
  closeEvidence: () => void;
  close: () => void;
}) {
  const { state, refresh } = useWorkbench();
  const comparison = state.comparisons.find((c) => c.key === experimentKey);
  const report = useRemote<TextReply>(
    results ? query('/api/artifact', { key: experimentKey, path: 'report.zh-CN.md' }) : undefined,
    4000,
  );
  const log = useRemote<TextReply>(
    !results && comparison?.job_id ? query('/api/log', { job: comparison.job_id }) : undefined,
    2000,
  );
  const stop = useAction();
  if (!comparison)
    return (
      <Modal open onCancel={close} footer={null}>
        <Alert type="info" message="正在读取实验配置…" />
      </Modal>
    );
  const live = active(comparison.status),
    done = comparison.report.groups.reduce((n, g) => n + g.scored, 0),
    total = comparison.report.case_count * comparison.report.groups.length;
  const cases = comparison.report.groups.flatMap((g) =>
    g.cases.map((c) => ({ ...c, groupKey: g.key, model: g.label, rowKey: g.key + '/' + c.id })),
  );
  const columns = [
    {
      title: '案例 / 模型',
      key: 'name',
      render: (_: unknown, c: (typeof cases)[number]) => (
        <>
          {c.id}
          <div className="muted">{c.model}</div>
        </>
      ),
    },
    { title: '状态', key: 'status', render: (_: unknown, c: Case) => caseLabel(c.status, live) },
    { title: '检视秒数', dataIndex: 'elapsed_seconds' },
    { title: '本例 F1', key: 'f1', render: (_: unknown, c: Case) => pct(c.metrics?.f1) },
    {
      title: '依据',
      key: 'evidence',
      render: (_: unknown, c: (typeof cases)[number]) => (
        <Button type="link" onClick={() => showEvidence(c.groupKey, c.id)}>
          查看状态与证据
        </Button>
      ),
    },
  ];
  return (
    <Modal
      open
      title={`${comparison.display_name ?? comparison.name} · ${results ? '结果对比' : '执行进度'}`}
      onCancel={close}
      width={1200}
      footer={null}
    >
      <ExperimentActions
        comparison={comparison}
        results={results}
        report={report.data}
        busy={stop.busy}
        onStop={() =>
          void stop.run(async () => {
            await api('/api/stop', { job: comparison.job_id });
            await refresh();
          })
        }
      />
      <Progress percent={Math.round((done / (total || 1)) * 100)} />
      <Typography.Paragraph>
        评分完成 {done}/{total} 次；共同评分案例 {comparison.report.common_cases.length}/
        {comparison.report.case_count} 个。
      </Typography.Paragraph>
      {results && (
        <Alert
          className="section-gap"
          type="info"
          message="P/R/F1 仅覆盖共同评分案例；请同时看完成率。Token 是已记录用量，包含缓存用量，覆盖范围不同不能直接比较费用。"
        />
      )}
      <GroupTable comparison={comparison} results={results} />
      <Table rowKey="rowKey" dataSource={cases} columns={columns} scroll={{ x: 900 }} />
      <ExperimentOutput results={results} report={report} log={log} stopError={stop.error} />
      {group && caseId && (
        <EvidenceDetail
          experimentKey={experimentKey}
          group={group}
          caseId={caseId}
          live={live}
          close={closeEvidence}
        />
      )}
    </Modal>
  );
}
function EvidenceDetail({
  experimentKey,
  group,
  caseId,
  live,
  close,
}: {
  experimentKey: string;
  group: string;
  caseId: string;
  live: boolean;
  close: () => void;
}) {
  const reply = useRemote<Evidence>(
    query('/api/comparison-case', { key: experimentKey, group, case: caseId }),
  );
  const [file, setFile] = useState('');
  const text = useRemote<TextReply>(
    file ? query('/api/artifact', { key: group, path: file }) : undefined,
  );
  const data = reply.data;
  return (
    <Modal open title={`${caseId} · 案例与评分依据`} width={1000} onCancel={close} footer={null}>
      <ErrorAlert error={reply.error} />
      {data ? (
        <>
          <Alert
            type="info"
            message={caseLabel(data.status.status, live)}
            description="以下读取已有意见与裁判记录，不重新评分。未匹配不等于误报。"
          />
          {data.diagnostic && (
            <Card title="诊断摘要">
              <p>
                已结束调用 {data.diagnostic.ended_calls} 次；实际重试 {data.diagnostic.retries} 次。
              </p>
              <pre className="code-view">{JSON.stringify(data.diagnostic, null, 2)}</pre>
            </Card>
          )}
          <EvidenceScore data={data} />
          <Form.Item label="原始证据文件">
            <Select
              className="full-width"
              value={file || undefined}
              options={data.artifacts.map((path) => ({ value: path, label: path }))}
              onChange={setFile}
            />
          </Form.Item>
          <ErrorAlert error={text.error} />
          <pre className="code-view">{text.data?.text ?? '选择文件查看。'}</pre>
        </>
      ) : (
        '正在读取证据…'
      )}
    </Modal>
  );
}
function EvidenceScore({ data }: { data: Evidence }) {
  if (data.score.status !== 'complete')
    return (
      <Alert
        className="section-gap"
        type="warning"
        message="尚无完整评分，不展示质量分数。已有答卷和文件仍可查看。"
      />
    );
  const pairs = data.score.metrics?.pairs ?? [],
    usedG = new Set(pairs.map((p) => p[0])),
    usedC = new Set(pairs.map((p) => p[1]));
  return (
    <>
      <Typography.Title level={4}>最终匹配 {pairs.length} 对</Typography.Title>
      {pairs.map(([gi, ci]) => (
        <Card
          key={`${gi}-${ci}`}
          title={`参考 G${gi + 1} ↔ 模型意见 C${ci + 1}`}
          className="section-gap"
        >
          <pre className="evidence-text">{data.references[gi]?.comment}</pre>
          <pre className="evidence-text">{data.comments[ci]?.content}</pre>
          <pre className="evidence-text">
            {data.score.decisions.find((d) => d.golden === gi && d.candidate === ci)?.reasoning ??
              '未记录裁判理由'}
          </pre>
        </Card>
      ))}
      <Typography.Title level={4}>未匹配模型意见</Typography.Title>
      {data.comments.map(
        (comment, i) =>
          !usedC.has(i) && (
            <Card
              key={i}
              title={`C${i + 1} · ${comment.path}:${comment.start_line}`}
              className="section-gap"
            >
              <pre className="evidence-text">{comment.content}</pre>
            </Card>
          ),
      )}
      <Typography.Title level={4}>未匹配参考意见</Typography.Title>
      {data.references.map(
        (reference, i) =>
          !usedG.has(i) && (
            <pre key={i} className="evidence-text">
              G{i + 1}：{reference.comment}
            </pre>
          ),
      )}
      <Typography.Title level={4}>全部意见对裁判判断</Typography.Title>
      <Table
        rowKey={(d) => `${d.golden}-${d.candidate}`}
        dataSource={data.score.decisions}
        columns={[
          {
            title: '意见对',
            key: 'pair',
            render: (_: unknown, d: Evidence['score']['decisions'][number]) =>
              `G${d.golden + 1} ↔ C${d.candidate + 1}`,
          },
          {
            title: '语义匹配',
            dataIndex: 'match',
            render: (match: boolean) => (match ? '匹配' : '不匹配'),
          },
          {
            title: '计入最终匹配',
            key: 'used',
            render: (_: unknown, d: Evidence['score']['decisions'][number]) =>
              pairs.some((p) => p[0] === d.golden && p[1] === d.candidate) ? '是' : '否',
          },
          { title: '裁判理由', dataIndex: 'reasoning' },
        ]}
      />
    </>
  );
}

function ExperimentActions({
  comparison,
  results,
  report,
  busy,
  onStop,
}: {
  comparison: Comparison;
  results: boolean;
  report?: TextReply;
  busy: boolean;
  onStop: () => void;
}) {
  const { state, openExperiment } = useWorkbench();
  return (
    <Space wrap className="section-gap">
      <Status status={comparison.status} />
      {!results && (
        <Button onClick={() => openExperiment(comparison.key, true)}>查看结果对比</Button>
      )}
      {!results && active(comparison.status) && (
        <Button danger disabled={state.access_role === 'viewer'} loading={busy} onClick={onStop}>
          停止实验
        </Button>
      )}
      {results && (
        <>
          <Button disabled={!report} onClick={() => download('模型对比报告.md', report!.text)}>
            下载报告
          </Button>
          <Button
            onClick={() =>
              download('模型对比明细.json', JSON.stringify(comparison.report, null, 2))
            }
          >
            下载明细 JSON
          </Button>
        </>
      )}
    </Space>
  );
}

function ExperimentOutput({
  results,
  report,
  log,
  stopError,
}: {
  results: boolean;
  report: { data?: TextReply; error?: string };
  log: { data?: TextReply; error?: string };
  stopError: string;
}) {
  if (results)
    return (
      <>
        <ErrorAlert error={report.error} />
        <Markdown text={report.data?.text ?? '报告尚未生成。'} />
      </>
    );
  return (
    <>
      <ErrorAlert error={log.error ?? stopError} />
      <pre className="code-view">{log.data?.text ?? '暂无执行日志。'}</pre>
    </>
  );
}
