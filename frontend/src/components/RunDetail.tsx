import { ReportView } from './ReportView';
import {
  Alert,
  App,
  Button,
  Collapse,
  Descriptions,
  Form,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Typography,
} from 'antd';
import { useEffect, useId, useState } from 'react';
import { api, download, downloadExport, query } from '../api';
import { useWorkbench } from '../context';
import { useAction } from '../hooks/useAction';
import { useRequestScope } from '../hooks/useRequestScope';
import { useRemote } from '../hooks/useRemote';
import {
  actionBlocked,
  actions,
  active,
  caseStatus,
  labels,
  pct,
  primary,
  runStages,
  stamp,
  suiteName,
} from '../presentation';
import { ErrorAlert, Status } from './Common';
import type { Case, Job, JobReply, Run, TextReply } from '../types';
function TextFile({ kind, run }: { kind: string; run: Run }) {
  const artifacts = kind === 'report' ? run.artifacts.filter((a) => a.report).sort((a, b) => Number(b.path === 'report.zh-CN.md') - Number(a.path === 'report.zh-CN.md')) : run.artifacts;
  const [chosen, setChosen] = useState('');
  const path = artifacts.some((a) => a.path === chosen) ? chosen : artifacts[0]?.path;
  const reply = useRemote<TextReply>(
    path ? query('/api/artifact', { key: run.key, path }) : undefined,
    4000,
  );
  const action = useAction();
  return (
    <>
      <Space wrap className="section-gap">
        <Select
          className="file-select"
          value={path}
          options={artifacts.map((a) => ({ value: a.path, label: a.path }))}
          onChange={setChosen}
          placeholder="暂无文件"
        />
        <Button
          disabled={!reply.data || reply.data.truncated}
          onClick={() => download(path?.split('/').pop() ?? 'result.txt', reply.data!.text)}
        >
          保存文件
        </Button>
        {kind === 'files' && run.suite !== 'ccrab' && (
          <Button
            loading={action.busy}
            onClick={() => void action.run(() => downloadExport(run.key))}
          >
            下载结果归档
          </Button>
        )}
      </Space>
      <ErrorAlert error={reply.error ?? action.error} />
      {reply.data?.truncated && (
        <Alert type="info" message="仅展示部分内容，完整文件保留在批次中。" />
      )}
      {kind === 'report' ? (
        <ReportView key={run.key + path} run={run} reply={reply.data} path={path} />
      ) : (
        <pre className="code-view">{reply.data?.text ?? '选择文件查看。'}</pre>
      )}
    </>
  );
}
function Logs({ jobs, initial }: { jobs: Job[]; initial?: string }) {
  const [choice, setChoice] = useState(initial);
  const save = useAction();
  useEffect(() => setChoice(initial), [initial]);
  const job = jobs.find((j) => j.id === choice) ?? jobs[0];
  const reply = useRemote<TextReply>(job ? query('/api/log', { job: job.id }) : undefined, 2000);
  return (
    <>
      <Space wrap className="section-gap">
        <Select
          className="file-select"
          value={job?.id}
          options={jobs.map((j) => ({
            value: j.id,
            label: `${stamp(j.created_at)} · ${actions[j.action] ?? j.action} · ${labels[j.status] ?? j.status}`,
          }))}
          onChange={setChoice}
        />
        <Button disabled={!job} loading={save.busy} onClick={() => void save.run(async () => {
          const latest = await api<TextReply>(query('/api/log', { job: job!.id }));
          download('execution.log', latest.text);
        })}>
          保存日志
        </Button>
      </Space>
      <Typography.Paragraph type="secondary">
        {job?.message} · {job?.step}
      </Typography.Paragraph>
      <ErrorAlert error={save.error || reply.error} />
      {reply.data?.truncated && (
        <Alert type="info" message="仅展示最近部分日志，完整日志仍保留。" />
      )}
      <pre className="code-view">{reply.data?.text ?? '此批次还没有页面执行日志。'}</pre>
    </>
  );
}
function Metrics({ run }: { run: Run }) {
  if (run.suite === 'ccrab')
    return (
      <Descriptions
        items={[
          { key: 'test', label: '修复后测试', children: pct(run.metrics?.test_pass_rate) },
          { key: 'repair', label: '修复执行', children: run.repair_executed ? '已执行' : '未执行' },
        ]}
      />
    );
  const metrics: [string, number | undefined][] = [
    ['Precision', run.metrics?.precision],
    ['Recall', run.metrics?.recall],
    ['F1', run.metrics?.f1],
  ];
  if (run.suite === 'mcr')
    metrics.push(
      ['缺陷状态准确率', run.metrics?.state_accuracy],
      ['状态转移准确率', run.metrics?.transition_accuracy],
      ['状态覆盖率', run.metrics?.state_coverage],
    );
  return (
    <Space wrap className="metrics">
      {metrics.map(([title, value]) => (
        <Statistic key={title} title={title} value={pct(value)} />
      ))}
    </Space>
  );
}
function ActionControls({
  run,
  onAction,
  pending,
  profile,
  setProfile,
}: {
  run: Run;
  onAction: (action: string, profile: string, task: string, retry: boolean) => void;
  pending: boolean;
  profile: string;
  setProfile: (value: string) => void;
}) {
  const { state } = useWorkbench();
  const controlId = useId();
  const [task, setTask] = useState(''),
    [retry, setRetry] = useState(false);
  const profiles = state.profiles.filter((p) => !p.internal || p.id === profile);
  return (
    <Collapse
      className="section-gap"
      items={[
        {
          key: 'actions',
          label: '分步执行、恢复与导出',
          children: (
            <>
              <Form layout="vertical">
                <Form.Item label="运行配置（需与批次模型一致）" htmlFor={controlId + "-profile"}>
                  <Select
                    id={controlId + "-profile"}
                    value={profile}
                    onChange={setProfile}
                    disabled={state.profiles.find((p) => p.id === profile)?.internal}
                    options={profiles.map((p) => ({
                      value: p.id,
                      label: `${p.label} · ${p.review_model}`,
                    }))}
                  />
                </Form.Item>
                {run.suite !== 'ccrab' && (
                  <>
                    <Form.Item label="执行范围" htmlFor={controlId + "-scope"}>
                      <Select
                        id={controlId + "-scope"}
                        value={task}
                        onChange={setTask}
                        options={[
                          { value: '', label: '全部案例 / 轮次' },
                          ...run.cases.map((c) => ({
                            value: c.id,
                            label: `${c.id} · ${labels[c.status] ?? c.status}`,
                          })),
                        ]}
                      />
                    </Form.Item>
                    {run.reviewer !== 'server_agent' && (
                      <Form.Item label="允许重跑所选未完成案例（可能产生费用）" htmlFor={controlId + "-retry"}>
                        <Switch id={controlId + "-retry"} checked={retry} onChange={setRetry} />
                      </Form.Item>
                    )}
                  </>
                )}
              </Form>
              <Space wrap>
                {runStages(run).map((action) => (
                  <Button
                    key={action}
                    disabled={pending || actionBlocked(state, run, action)}
                    onClick={() => onAction(action, profile, task, retry)}
                  >
                    {actions[action]}
                  </Button>
                ))}
              </Space>
            </>
          ),
        },
      ]}
    />
  );
}
function Overview({
  run,
  latest,
  onAction,
  pending,
  profile,
  setProfile,
}: {
  run: Run;
  latest?: Job;
  onAction: (action: string, profile: string, task: string, retry: boolean) => void;
  pending: boolean;
  profile: string;
  setProfile: (value: string) => void;
}) {
  return (
    <>
      <Alert
        type="info"
        message={`${run.completed}/${run.total} 份完整答卷已保存`}
        description={`评分覆盖 ${run.scored_cases ?? '以评分文件为准'}；未完成案例不视为零分。${latest?.message ?? ''}`}
      />
      {run.score_locked_reason && (
        <Alert className="section-gap" type={run.status === 'partial_scored' ? 'warning' : 'info'} message={run.score_locked_reason} />
      )}
      <Metrics run={run} />
      <ActionControls
        run={run}
        onAction={onAction}
        pending={pending}
        profile={profile}
        setProfile={setProfile}
      />
      <Table
        rowKey="id"
        dataSource={run.cases}
        columns={[
          { title: '案例 / 轮次', dataIndex: 'id' },
          { title: '仓库', dataIndex: 'repo' },
          { title: '检视覆盖', dataIndex: 'review_coverage', render: (value?: string) => value ?? '未提供' },
          { title: '失败 / 跳过原因', render: (_: unknown, item: Case) => <CoverageDetails item={item} /> },
          {
            title: '状态',
            dataIndex: 'status',
            render: (status: string, item: Case) => (
              <Status status={caseStatus(status, latest, item.id)} />
            ),
          },
        ]}
        pagination={{ pageSize: 10 }}
        scroll={{ x: 700 }}
      />
    </>
  );
}
function CoverageDetails({ item }: { item: Case }) {
  const details = item.review_details ?? [];
  if (!details.length) return '—';
  return <Collapse size="small" items={[{ key: 'coverage', label: `${details.length} 项明细`,
    children: <Space direction="vertical">{details.map((row, index) =>
      <div key={index}><Typography.Text strong>{row.kind} · {row.file ?? '未提供文件'}</Typography.Text>
        <div>{row.message}</div></div>)}</Space> }]} />;
}

export function RunDetail({
  runKey,
  jobId,
  currentTab,
  onTab,
  close,
}: {
  runKey: string;
  jobId?: string;
  currentTab: string;
  onTab: (tab: string, job?: string) => void;
  close: () => void;
}) {
  const { state, refresh, importBusy } = useWorkbench();
  const { modal } = App.useApp();
  const { run: perform, busy, error } = useAction();
  const ownRequest = useRequestScope(runKey);
  const record = state.runs.find((r) => r.key === runKey);
  const [selectedProfile, setProfile] = useState<string>();
  const profile = selectedProfile ?? profileForRun(record, state.profiles);
  const jobs = state.jobs.filter((j) => j.key === runKey),
    latest = jobs.find((j) => active(j.status)) ?? jobs[0];
  const choice = primary(record, latest, state.queue_mode);
  const execute = (action: string, profile: string, task: string, retry: boolean) =>
    perform(async () => {
      const current = ownRequest();
      if (!record || actionBlocked(state, record, action) || importBusy)
        throw new Error('当前批次不可执行，请检查任务和版本状态');
      if (action === 'workflow' && task)
        throw new Error('一键流程请选全部案例；单例请使用检视按钮');
      const result = await api<JobReply>('/api/jobs', {
        action,
        key: runKey,
        profile,
        task: task || undefined,
        retry: record.suite === 'ccrab' ? false : retry,
        confirmed: action === 'resume' ? true : undefined,
      });
      await refresh();
      if (current()) onTab('log', result.job_id);
    });
  const start = (action: string, profile: string, task = '', retry = false) => {
    if (record?.case_resources_purged && ['review', 'workflow', 'resume', 'recover', 'eval', 'report'].includes(action)) {
      modal.warning({
        title: action === 'eval' ? '无法重新评分' : action === 'report' ? '无法重新生成报告' : '无法续跑检视',
        content: '这个批次依赖的案例集或仓库资源已被永久清理。历史日志、答卷、已保存评分和已生成报告仍可查看，但不能继续检视、补跑失败案例或重新评分。请重新导入对应案例集和仓库版本，并新建批次。',
        okText: '知道了',
      });
      return;
    }
    if (['review', 'eval', 'report', 'workflow', 'resume', 'compare-martian'].includes(action)) {
      modal.confirm({
        title: `开始${actions[action]}？`,
        content: actionDescription(action, task, record?.score_locked_reason),
        okText: '开始执行',
        cancelText: '取消',
        onOk: () => execute(action, profile, task, retry),
      });
    } else void execute(action, profile, task, retry);
  };
  const primaryClick = () =>
    choice.action ? start(choice.action, profile) : onTab(choice.tab ?? 'overview');
  return (
    <Modal
      open
      title={detailTitle(record)}
      onCancel={close}
      width={1120}
      footer={null}
      className="detail-modal"
    >
      <ErrorAlert error={error} />
      {record && (
        <RunHeader
          record={record}
          latest={latest}
          choice={choice}
          disabled={busy || importBusy}
          onPrimary={primaryClick}
          onStop={() =>
            void perform(async () => {
              await api('/api/stop', { job: latest?.id });
              await refresh();
            })
          }
        />
      )}
      {!record && latest && (
        <PendingRun latest={latest} busy={busy} onStop={() => void perform(async () => {
          await api('/api/stop', { job: latest.id });
          await refresh();
        })} />
      )}
      <Tabs
        activeKey={currentTab}
        onChange={(tab) => onTab(tab)}
        destroyOnHidden
        items={[
          {
            key: 'overview',
            label: '概览与案例',
            children: record ? (
              <Overview
                run={record}
                latest={latest}
                onAction={start}
                pending={busy || importBusy}
                profile={profile}
                setProfile={setProfile}
              />
            ) : (
              <Alert type="info" message="配置正在排队保存，可在执行日志查看进度。" />
            ),
          },
          { key: 'log', label: '执行日志', children: <Logs jobs={jobs} initial={jobId} /> },
          {
            key: 'report',
            label: '中文报告',
            children: record && <TextFile kind="report" run={record} />,
          },
          {
            key: 'files',
            label: '原始文件',
            children: record && <TextFile kind="files" run={record} />,
          },
        ]}
      />
    </Modal>
  );
}

function detailTitle(record?: Run) {
  return record?.display_name ?? record?.name ?? '正在保存测评配置';
}
function PendingRun({ latest, busy, onStop }: { latest: Job; busy: boolean; onStop: () => void }) {
  const { state } = useWorkbench();
  if (!active(latest.status)) return null;
  return (
    <Space className="section-gap">
      <Typography.Text>{latest.message}</Typography.Text>
      <Button danger loading={busy} disabled={state.access_role === 'viewer' || latest.status === 'cancelling'} onClick={onStop}>
        {latest.status === 'queued' ? '取消排队' : '停止任务'}
      </Button>
    </Space>
  );
}

function RunHeader({
  record,
  latest,
  choice,
  disabled,
  onPrimary,
  onStop,
}: {
  record: Run;
  latest?: Job;
  choice: ReturnType<typeof primary>;
  disabled: boolean;
  onPrimary: () => void;
  onStop: () => void;
}) {
  const { state } = useWorkbench();
  return (
    <>
      <Typography.Paragraph type="secondary">
        {suiteName(state, record.suite)} · {record.model} · 裁判 {record.judge_model} /{' '}
        {record.evaluator}
      </Typography.Paragraph>
      {record.compatibility_warning && (
        <Alert type="warning" message={record.compatibility_warning} />
      )}
      <Space wrap className="section-gap">
        <Button
          type="primary"
          disabled={disabled || (!!choice.action && actionBlocked(state, record, choice.action))}
          onClick={onPrimary}
        >
          {choice.label}
        </Button>
        {latest && active(latest.status) && (
          <Button
            danger
            disabled={state.access_role === 'viewer' || disabled || latest.status === 'cancelling'}
            onClick={onStop}
          >
            停止任务
          </Button>
        )}
      </Space>
    </>
  );
}

function profileForRun(record: Run | undefined, profiles: { id: string; review_model: string }[]) {
  return record?.profile ?? profiles.find((p) => p.review_model === record?.model)?.id ?? '';
}

function actionDescription(action: string, task: string, scoreLock?: string) {
  if (action === 'resume')
    return '请确认旧扫描进程和容器已停止。将解除队列暂停，跳过完整答卷，继续未完成案例；尚未评分时完成后评分。可能产生模型用量。' + (scoreLock ?? '');
  const scope = action === 'eval' ? '所有完整答卷；未完成案例排除' : task || '全部案例 / 轮次';
  const warning = ['review', 'workflow'].includes(action) ? scoreLock ?? '' : '';
  return `执行范围：${scope}。将调用模型，可能产生 API 用量。${warning}`;
}
