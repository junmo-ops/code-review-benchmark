import { Button, Card, Col, Input, Progress, Row, Space, Table, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { useWorkbench } from '../context';
import { canSubmit, pct, stamp, suiteName } from '../presentation';
import { Heading, Status } from '../components/Common';
import type { Run } from '../types';
export function Runs() {
  const { state, newRun, openRun, importBusy } = useWorkbench();
  const [filter, setFilter] = useState(''),
    [search, setSearch] = useState('');
  const grouped = new Set(state.comparisons.flatMap((c) => c.report.groups.map((g) => g.key)));
  const runs = state.runs
    .filter((r) => !grouped.has(r.key))
    .filter(
      (r) =>
        !filter ||
        r.suite === filter ||
        (filter === 'swe_prbench_llm' && r.suite === 'swe_prbench'),
    )
    .filter((r) =>
      `${r.display_name ?? r.name} ${r.model} ${suiteName(state, r.suite)}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    );
  const columns = [
    {
      title: '批次 / 案例集',
      key: 'name',
      render: (_: unknown, r: Run) => (
        <>
          <Button type="link" onClick={() => openRun(r.key)}>
            {r.display_name ?? r.name}
          </Button>
          <div className="muted">
            {suiteName(state, r.suite)} · {stamp(r.created)}
          </div>
        </>
      ),
    },
    {
      title: '被测对象',
      dataIndex: 'model',
      render: (model: string, r: Run) => (
        <>
          {model}
          <div className="muted">
            {r.reviewer} · {r.evaluator}
          </div>
        </>
      ),
    },
    {
      title: '检视进度',
      key: 'progress',
      render: (_: unknown, r: Run) => (
        <>
          <span>
            {r.completed}/{r.total} {r.suite === 'mcr' ? '轮' : '例'}
          </span>
          <Progress
            percent={Math.round((r.completed / (r.total || 1)) * 100)}
            size="small"
            showInfo={false}
          />
        </>
      ),
    },
    { title: '状态', dataIndex: 'status', render: (s: string) => <Status status={s} /> },
    {
      title: 'F1 / 测试',
      key: 'score',
      render: (_: unknown, r: Run) =>
        r.suite === 'ccrab' ? pct(r.metrics?.test_pass_rate) : pct(r.metrics?.f1),
    },
  ];
  return (
    <>
      <Heading
        title="单模型测评"
        description="选择一个模型运行测评；不同案例集分别报告结果。"
        action={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!canSubmit(state) || importBusy}
            onClick={() => newRun(filter)}
          >
            新建测评
          </Button>
        }
      />
      <Row gutter={[16, 16]} className="section-gap">
        {state.catalog.map((s) => (
          <Col key={s.id} xs={24} md={12} xl={8}>
            <Card
              hoverable
              className={filter === s.id ? 'selected-card' : ''}
              onClick={() => setFilter(filter === s.id ? '' : s.id)}
              title={s.name}
              extra={s.role}
            >
              <Typography.Paragraph type="secondary">{s.description}</Typography.Paragraph>
              {s.datasets.length} 个案例范围
            </Card>
          </Col>
        ))}
      </Row>
      <Card
        title={`测评批次 · ${runs.length}`}
        extra={
          <Input.Search
            allowClear
            placeholder="搜索批次或模型"
            onChange={(event) => setSearch(event.target.value)}
          />
        }
      >
        <Table
          rowKey="key"
          dataSource={runs}
          columns={columns}
          scroll={{ x: 900 }}
          pagination={{ pageSize: 10, showSizeChanger: true }}
        />
      </Card>
    </>
  );
}
export function Jobs() {
  const { state, openRun, openExperiment } = useWorkbench();
  const columns = [
    { title: '操作', dataIndex: 'action' },
    { title: '阶段', dataIndex: 'step' },
    { title: '状态', dataIndex: 'status', render: (s: string) => <Status status={s} /> },
    { title: '说明', dataIndex: 'message' },
    { title: '创建时间', dataIndex: 'created_at', render: stamp },
    {
      title: '日志',
      key: 'log',
      render: (_: unknown, job: (typeof state.jobs)[number]) => (
        <Button
          type="link"
          onClick={() =>
            state.comparisons.some((c) => c.key === job.key)
              ? openExperiment(job.key)
              : openRun(job.key, job.id)
          }
        >
          查看记录
        </Button>
      ),
    },
  ];
  return (
    <>
      <Heading title="执行记录" description="任务阶段、执行状态与日志持续保存。" />
      <Space direction="vertical" className="full-width">
        <Table rowKey="id" columns={columns} dataSource={state.jobs} scroll={{ x: 900 }} />
      </Space>
    </>
  );
}
