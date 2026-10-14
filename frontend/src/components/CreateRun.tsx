import { Alert, Button, Collapse, Form, Input, InputNumber, Modal, Select, Space } from 'antd';
import { useEffect, useRef } from 'react';
import { creationName, submitCreation } from '../creation';
import { useWorkbench } from '../context';
import { availableProfiles, canSubmit } from '../presentation';
import { useAction } from '../hooks/useAction';
import { useRequestScope } from '../hooks/useRequestScope';
import { ErrorAlert } from './Common';
import type { Snapshot } from '../types';
interface Values {
  suite: string;
  dataset: string;
  name: string;
  display_name?: string;
  review_profile: string;
  judge_profile?: string;
  reviewer: string;
  evaluator: string;
  timeout_minutes: number;
  case_jobs: number;
  max_case_retries: number;
  max_tokens: number;
}
const identifier = (suite: string) => creationName(suite === 'swe_prbench_llm' ? 'swe' : suite);
function defaults(state: Snapshot, suiteId?: string, datasetId?: string): Values {
  const suite = state.catalog.find((s) => s.id === suiteId) ??
    state.catalog[0] ?? { id: '', datasets: [] };
  const profile = availableProfiles(state).find((p) => p.review_ready)?.id ?? '';
  return {
    suite: suite.id,
    dataset: datasetId ?? suite.datasets[0]?.id ?? '',
    name: identifier(suite.id || 'run'),
    review_profile: profile,
    judge_profile: profile,
    reviewer: suite.id === 'mcr' ? 'llm_direct' : 'lcr',
    evaluator:
      suite.id === 'ccrab' ? 'executable' : suite.id === 'codefuse_cr' ? 'martian' : 'original',
    timeout_minutes: 10,
    case_jobs: 1,
    max_case_retries: 1,
    max_tokens: 32768,
  };
}
function reviewers(suite: string) {
  if (suite === 'mcr')
    return [
      { value: 'llm_direct', label: '直接模型（Defect Card）' },
      { value: 'lcr', label: 'LCR（暂不支持缺陷状态判断）' },
    ];
  const result = [{ value: 'lcr', label: 'LCR CLI' }];
  if (suite !== 'ccrab') result.push({ value: 'server_agent', label: '服务端 Agent' });
  if (suite === 'aacr')
    result.push({ value: 'cline_skill', label: 'Cline + Skill（Mac 单例试跑）' });
  return result;
}
function evaluators(suite: string) {
  if (suite === 'ccrab') return [{ value: 'executable', label: '发布测试' }];
  if (suite === 'mcr') return [{ value: 'original', label: 'MCR 一对一 + 状态' }];
  const result = [{ value: 'martian', label: 'Martian 语义评分' }];
  if (suite !== 'codefuse_cr') result.push({ value: 'original', label: '案例集原始评分' });
  return result;
}
function judgeChoices(state: Snapshot) {
  const profiles = availableProfiles(state),
    choices = profiles.map((p) => ({ value: p.id, label: `${p.label} · ${p.review_model}` }));
  for (const p of profiles)
    if (
      p.judge_model &&
      p.judge_model !== '未设置' &&
      !profiles.some((v) => v.review_model === p.judge_model)
    )
      choices.push({ value: 'legacy-judge:' + p.id, label: p.judge_model + ' · 旧裁判配置' });
  return choices;
}
function ready(state: Snapshot, values: Partial<Values>) {
  const model = state.profiles.find((p) => p.id === values.review_profile);
  const judgeId = values.judge_profile ?? '',
    legacy = judgeId.startsWith('legacy-judge:');
  const judge = state.profiles.find((p) => p.id === judgeId.replace('legacy-judge:', ''));
  return (
    canSubmit(state) &&
    !!model?.review_ready &&
    (values.suite === 'ccrab' || (legacy ? !!judge?.ready : !!judge?.review_ready))
  );
}
function Advanced({ suite, reviewer }: { suite: string; reviewer: string }) {
  const concurrent = !['mcr', 'ccrab'].includes(suite) && reviewer === 'lcr';
  return (
    <Collapse
      items={[
        {
          key: 'advanced',
          label: '高级参数',
          children: (
            <>
              {!['mcr', 'ccrab'].includes(suite) && (
                <Form.Item
                  name="timeout_minutes"
                  label={
                    reviewer === 'lcr'
                      ? 'LCR 每文件任务时限（分钟）'
                      : '每案例执行或等待时限（分钟）'
                  }
                  rules={[{ required: true }]}
                >
                  <InputNumber min={1} max={120} className="full-width" />
                </Form.Item>
              )}
              {concurrent && (
                <>
                  <Form.Item
                    name="case_jobs"
                    label="案例并发"
                    extra="同时运行的案例数；每案例内部文件顺序检视。"
                    rules={[{ required: true }]}
                  >
                    <InputNumber min={1} max={16} precision={0} className="full-width" />
                  </Form.Item>
                  <Form.Item
                    name="max_case_retries"
                    label="失败案例自动重试次数"
                    extra="可能增加模型调用和耗时。"
                  >
                    <InputNumber min={0} max={2} precision={0} className="full-width" />
                  </Form.Item>
                </>
              )}
              {suite === 'mcr' && (
                <Form.Item name="max_tokens" label="直接模型输出上限">
                  <InputNumber min={1} max={131072} className="full-width" />
                </Form.Item>
              )}
            </>
          ),
        },
      ]}
    />
  );
}
export function CreateRun({
  selection,
  close,
}: {
  selection?: { suite?: string; dataset?: string };
  close: () => void;
}) {
  const { state, refresh, openRun, importBusy } = useWorkbench();
  const [form] = Form.useForm<Values>();
  const values = Form.useWatch([], form) ?? {};
  const { run, busy, error, clear } = useAction();
  const initialized = useRef<typeof selection>(undefined);
  const ownRequest = useRequestScope(selection);
  useEffect(() => {
    if (!selection) {
      initialized.current = undefined;
      return;
    }
    if (initialized.current === selection) return;
    initialized.current = selection;
    form.resetFields();
    form.setFieldsValue(defaults(state, selection.suite, selection.dataset));
    clear();
  }, [selection, form, state, clear]);
  const suite = state.catalog.find((s) => s.id === values.suite),
    dataset = suite?.datasets.find((d) => d.id === values.dataset);
  const submit = (start: boolean) =>
    run(async () => {
      const current = ownRequest();
      const data = await form.validateFields();
      const key = ['mcr', 'ccrab'].includes(data.suite) ? data.suite + '/' + data.name : data.name;
      const result = await submitCreation('/api/jobs', {
        ...data,
        action: 'create',
        start_after_create: start,
        case_jobs: data.reviewer === 'lcr' ? data.case_jobs : 1,
        max_case_retries: data.reviewer === 'lcr' ? data.max_case_retries : 0,
        ...(data.suite === 'ccrab' ? { judge_profile: undefined } : {}),
      }, key, 'create');
      await refresh();
      if (!current()) return;
      close();
      openRun(result.key, result.job_id);
    });
  const changeSuite = (id: string) => {
    const initial = defaults(state, id);
    form.setFieldsValue({
      suite: id,
      dataset: initial.dataset,
      name: initial.name,
      reviewer: initial.reviewer,
      evaluator: initial.evaluator,
    });
  };
  const enabled = ready(state, values) && !importBusy;
  return (
    <Modal
      open={!!selection}
      title="新建测评"
      onCancel={close}
      width={640}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={close}>取消</Button>
          <Button disabled={!enabled || busy} onClick={() => void submit(false)}>
            仅保存配置
          </Button>
          <Button
            type="primary"
            loading={busy}
            disabled={!enabled}
            onClick={() => void submit(true)}
          >
            创建并开始
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Form.Item name="suite" label="案例集" rules={[{ required: true }]}>
          <Select
            options={state.catalog.map((s) => ({ value: s.id, label: s.name }))}
            onChange={changeSuite}
          />
        </Form.Item>
        <Form.Item name="dataset" label="案例范围" rules={[{ required: true }]}>
          <Select
            options={suite?.datasets.map((d) => ({
              value: d.id,
              label: `${d.name ?? d.id} · ${d.count} ${d.rounds ? `PR / ${d.rounds} 轮` : '例'}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="display_name" label="测评名称（选填）">
          <Input maxLength={80} />
        </Form.Item>
        <Form.Item name="name" label="测评编号">
          <Input readOnly />
        </Form.Item>
        <Form.Item name="review_profile" label="被测模型" rules={[{ required: true }]}>
          <Select
            options={availableProfiles(state).map((p) => ({
              value: p.id,
              label: `${p.label} · ${p.review_model}`,
            }))}
          />
        </Form.Item>
        {values.suite !== 'ccrab' && (
          <Form.Item name="judge_profile" label="裁判模型" rules={[{ required: true }]}>
            <Select options={judgeChoices(state)} />
          </Form.Item>
        )}
        <Form.Item name="reviewer" label="被测工具">
          <Select options={reviewers(values.suite ?? '')} />
        </Form.Item>
        <Form.Item name="evaluator" label="评分方法">
          <Select options={evaluators(values.suite ?? '')} />
        </Form.Item>
        <Advanced suite={values.suite ?? ''} reviewer={values.reviewer ?? ''} />
        <Alert
          className="section-gap"
          type="info"
          message={`${dataset?.count ?? 0} 个案例；保存配置不调用模型，创建并开始会调用模型并产生 API 用量。`}
          description={
            values.suite === 'ccrab'
              ? '固定单例检视、DeepSeek 修复与容器测试，不使用语义裁判。'
              : 'LCR 整例上限随变更文件数增加；与多模型实验的整例预算不同。'
          }
        />
        {!enabled && (
          <Alert type="warning" message="请准备可用的被测模型和裁判，并等待资源导入完成。" />
        )}
        <ErrorAlert error={error} />
      </Form>
    </Modal>
  );
}
