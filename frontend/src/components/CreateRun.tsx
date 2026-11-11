import { Alert, Button, Collapse, Form, Input, InputNumber, Modal, Select, Space, Typography } from 'antd';
import { useEffect, useRef } from 'react';
import { creationName, submitCreation } from '../creation';
import { useWorkbench } from '../context';
import { availableProfiles, canSubmit } from '../presentation';
import { useAction } from '../hooks/useAction';
import { useRequestScope } from '../hooks/useRequestScope';
import { useRemote } from '../hooks/useRemote';
import { DshRuntimeWarning, ErrorAlert } from './Common';
import type { SkillListing, Snapshot } from '../types';
// Internal marker for the explicit baseline option inside the member multi-select.
const BASELINE_OPTION = '__baseline__';
interface Values {
  evaluation_mode: 'quality' | 'stability';
  suite: string;
  dataset: string;
  name: string;
  display_name?: string;
  review_profile: string;
  judge_profile?: string;
  reviewer: string;
  skill_mode?: 'baseline' | 'skill';
  skill_versions?: string[];
  evaluator: string;
  timeout_minutes: number;
  case_jobs: number;
  max_case_retries: number;
  max_tokens: number;
  dsh_max_output_tokens?: number;
}
const identifier = (suite: string) => creationName(suite === 'swe_prbench_llm' ? 'swe' : suite);
const skillMode = (values: Values) => values.reviewer === 'dsh_skill' ? values.skill_mode : undefined;
function defaults(state: Snapshot, suiteId?: string, datasetId?: string): Values {
  const suite = state.catalog.find((s) => s.id === suiteId) ??
    state.catalog[0] ?? { id: '', datasets: [] };
  const profile = availableProfiles(state).find((p) => p.review_ready)?.id ?? '';
  return {
    evaluation_mode: 'quality',
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
  if (suite === 'aacr') {
    result.push({ value: 'cline_skill', label: 'Cline + Skill（Mac 单例试跑）' });
    result.push({ value: 'dsh_skill', label: 'DSH + Skill' });
  }
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
function SkillStateHint({ mode, selected, labelOf }: {
  mode?: 'baseline' | 'skill';
  selected: string[];
  labelOf: (id: string) => string;
}) {
  if (mode !== 'skill') return null;
  if (selected.length !== 1) {
    if (selected.length > 1) {
      return (
        <Alert type="info" message={`Skill 组合（${selected.length} 个成员）：${selected.map(labelOf).join('、')}。每个案例的新会话将逐一加载并留证。`} />
      );
    }
    return null;
  }
  return <Typography.Paragraph type="secondary" className="section-gap">单 Skill：{labelOf(selected[0])}。</Typography.Paragraph>;
}

function SkillSelection({ reviewer }: { reviewer?: string }) {
  const form = Form.useFormInstance<Values>();
  const mode = Form.useWatch('skill_mode', form);
  const raw = Form.useWatch('skill_versions', form);
  const { data, error } = useRemote<SkillListing>(reviewer === 'dsh_skill' ? '/api/skills' : undefined);
  if (reviewer !== 'dsh_skill') return null;
  const selected = Array.isArray(raw) ? raw : [];
  const supported = !!data?.supported;
  const names = new Map((data?.versions ?? []).map((version) => [version.id, version.name]));
  const labelOf = (id: string) => names.get(id) ?? `${id.slice(0, 12)}…`;
  // 三态都由这一个多选控件表达：未选任何版本（基线选项）→ 基线；1 个 → 单 Skill；≥2 个 → Skill 组合。
  const shown = mode === 'skill' ? selected : [BASELINE_OPTION];
  const options = [
    { value: BASELINE_OPTION, label: '不加载 Skill（基线）' },
    ...(data?.versions ?? []).map((version) => ({
      value: version.id,
      label: `${version.name} · ${version.id.slice(0, 12)}`,
      disabled: !supported,
    })),
  ];
  // 基线与成员集合互斥：点基线清空成员，点成员清掉基线（沿用「切换即清空」行为）。
  const choose = (next: string[]) => {
    const wasBaseline = form.getFieldValue('skill_mode') !== 'skill';
    if (!wasBaseline && next.includes(BASELINE_OPTION)) {
      form.setFieldsValue({ skill_mode: 'baseline', skill_versions: [] });
      return;
    }
    const versions = next.filter((value) => value !== BASELINE_OPTION);
    form.setFieldsValue({ skill_mode: versions.length ? 'skill' : 'baseline', skill_versions: versions });
  };
  return (
    <>
      <Form.Item name="skill_mode" hidden preserve={false} initialValue="baseline" rules={[{ required: true }]} />
      <Form.Item name="skill_versions" hidden preserve={false} initialValue={[]} />
      <Form.Item
        label="Skill 选择"
        required
        extra="选 0 个（基线）= 无待测 Skill 基线；选 1 个 = 单 Skill；选 ≥2 个 = Skill 组合。基线与成员集合互斥，切换时清空对端。"
      >
        <Select
          mode="multiple"
          value={shown}
          onChange={choose}
          loading={!data && !error}
          options={options}
          optionFilterProp="label"
          placeholder="选择 Skill 版本（留空则为本批次基线）"
        />
      </Form.Item>
      <SkillStateHint mode={mode} selected={selected} labelOf={labelOf} />
      {data && !data.supported && <Alert type="warning" message={data.message} />}
      <ErrorAlert error={error} />
    </>
  );
}
function executionDescription(values: Partial<Values>) {
  if (values.suite === 'ccrab') return '固定单例检视、DeepSeek 修复与容器测试，不使用语义裁判。';
  if (values.reviewer === 'server_agent') return '默认等待服务端结果 60 分钟，可在高级参数调整。超时只停止本地等待，不会取消服务端任务。';
  if (values.reviewer === 'lcr') return 'LCR 整例上限随变更文件数增加；与多模型实验的整例预算不同。';
  return undefined;
}
function EvaluationMode({ suite }: { suite: string }) {
  const form = Form.useFormInstance<Values>();
  const unsupported = ['mcr', 'ccrab'].includes(suite);
  return <>
    <Form.Item name="evaluation_mode" label="测评目的" rules={[{ required: true }]}>
      <Select onChange={(value: string) => form.setFieldValue('max_case_retries', value === 'stability' ? 0 : 1)} options={[
        { value: 'quality', label: '检视质量：允许配置平台补救' },
        { value: 'stability', label: '运行稳定性：平台不自动补跑', disabled: unsupported },
      ]} />
    </Form.Item>
    <Alert type="info" className="section-gap" message="测评目的随批次固定，补跑不改写首次执行结果。"
      description={unsupported ? 'MCR 多轮和 c-CRAB 修复测试暂未接入运行稳定性口径。'
        : '稳定性模式关闭平台自动重跑、LCR 会话续跑和 DSH 压缩失败/溢出补救；保留证据恢复和原任务查询。实际运行条件仍需与线上核对。'} />
  </>;
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
  const form = Form.useFormInstance<Values>();
  const stability = Form.useWatch('evaluation_mode', form) === 'stability';
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
                    <InputNumber min={0} max={2} precision={0} className="full-width" disabled={stability} />
                  </Form.Item>
                </>
              )}
              {suite === 'mcr' && (
                <Form.Item name="max_tokens" label="直接模型输出上限">
                  <InputNumber min={1} max={131072} className="full-width" />
                </Form.Item>
              )}
              {reviewer === 'dsh_skill' && (
                <Form.Item name="dsh_max_output_tokens" label="DSH 单次输出上限（tokens）"
                  initialValue={8192} preserve={false} rules={[{ required: true }]}>
                  <InputNumber min={1} max={65536} precision={0} step={1024} className="full-width" />
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
        max_case_retries: data.reviewer === 'lcr' && data.evaluation_mode !== 'stability' ? data.max_case_retries : 0,
        skill_mode: skillMode(data),
        // 统一发成员列表（新契约）；基线不发空数组（后端 creation 拒绝空列表，仅按 skill_mode 判别）。
        skill_versions: data.reviewer === 'dsh_skill' && (data.skill_versions?.length ?? 0)
          ? data.skill_versions : undefined,
        dsh_max_output_tokens: data.reviewer === 'dsh_skill' ? data.dsh_max_output_tokens : undefined,
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
      evaluation_mode: 'quality',
      dataset: initial.dataset,
      name: initial.name,
      reviewer: initial.reviewer,
      timeout_minutes: initial.timeout_minutes,
      evaluator: initial.evaluator,
      skill_mode: initial.reviewer === 'dsh_skill' ? 'baseline' : undefined,
      skill_versions: initial.reviewer === 'dsh_skill' ? [] : undefined,
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
          <Select options={reviewers(values.suite ?? '')}
            onChange={(reviewer: string) => form.setFieldsValue({ timeout_minutes: reviewer === 'server_agent' ? 60 : 10, skill_mode: reviewer === 'dsh_skill' ? 'baseline' : undefined, skill_versions: reviewer === 'dsh_skill' ? [] : undefined })} />
        </Form.Item>
        <EvaluationMode suite={values.suite ?? ''} />
        <SkillSelection reviewer={values.reviewer} />
        <DshRuntimeWarning reviewer={values.reviewer} mode={state.dsh_runtime_mode} />
        <Form.Item name="evaluator" label="评分方法">
          <Select options={evaluators(values.suite ?? '')} />
        </Form.Item>
        <Advanced suite={values.suite ?? ''} reviewer={values.reviewer ?? ''} />
        <Alert
          className="section-gap"
          type="info"
          message={`${dataset?.count ?? 0} 个案例；保存配置不调用模型，创建并开始会调用模型并产生 API 用量。`}
          description={executionDescription(values)}
        />
        {!enabled && (
          <Alert type="warning" message="请准备可用的被测模型和裁判，并等待资源导入完成。" />
        )}
        <ErrorAlert error={error} />
      </Form>
    </Modal>
  );
}
