import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Tag,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useRef, useState } from 'react';
import { api } from '../api';
import { useWorkbench } from '../context';
import { availableProfiles, editable } from '../presentation';
import { ErrorAlert, Heading, TestResults } from '../components/Common';
import { useAction } from '../hooks/useAction';
import type { Connection, ModelValues, Profile } from '../types';
export function Models() {
  const { state, refresh } = useWorkbench();
  const { message, modal } = App.useApp();
  const [editing, setEditing] = useState<Profile | null | undefined>();
  const [form] = Form.useForm<ModelValues>();
  const save = useAction(),
    test = useAction();
  const [results, setResults] = useState<Record<string, Connection>>({});
  const [draftResult, setDraftResult] = useState<Connection>();
  const generation = useRef(0);
  const invalidate = () => {
    generation.current++;
    setDraftResult(undefined);
  };
  const open = (profile: Profile | null) => {
    invalidate();
    save.clear();
    test.clear();
    setEditing(profile);
    form.resetFields();
    form.setFieldsValue({
      provider: profile?.provider ?? 'deepseek',
      name: profile?.label,
      model: profile?.review_model,
      base_url: profile?.base_url,
      stream: profile?.stream_enabled ?? false,
      api_key: '',
    });
  };
  const modelData = (values: ModelValues) => ({ ...values, stream: String(values.stream) });
  const testProfile = (id: string) =>
    test.run(async () => {
      const result = await api<Connection>('/api/profiles/test', { profile_id: id });
      setResults((old) => ({ ...old, [id]: result }));
    });
  const testDraft = () =>
    test.run(async () => {
      const values = await form.validateFields(),
        version = generation.current;
      const result = await api<Connection>('/api/profiles/test', {
        ...modelData(values),
        ...(editing ? { profile_id: editing.id } : {}),
      });
      if (version === generation.current) setDraftResult(result);
    });
  const submit = (values: ModelValues) =>
    save.run(async () => {
      await api(
        editing ? '/api/profiles/' + encodeURIComponent(editing.id) : '/api/profiles',
        modelData(values),
        editing ? 'PUT' : 'POST',
      );
      invalidate();
      setEditing(undefined);
      await refresh();
      message.success('配置已保存，未调用模型；已有批次保留原配置。');
    });
  const remove = (profile: Profile) =>
    modal.confirm({
      title: `删除“${profile.label}”？`,
      content: '已有批次保留创建时的配置快照。',
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        await api('/api/profiles/' + encodeURIComponent(profile.id), undefined, 'DELETE');
        await refresh();
        message.success('模型配置已删除');
      },
    });
  const profiles = availableProfiles(state);
  return (
    <>
      <Heading
        title="模型配置"
        description="维护模型与裁判接口；配置按现有规则持久保存，密钥不回显。"
        action={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!editable(state)}
            onClick={() => open(null)}
          >
            新增模型
          </Button>
        }
      />
      <ErrorAlert error={test.error} />
      <Row gutter={[16, 16]}>
        {profiles.map((p) => (
          <Col xs={24} lg={12} xl={8} key={p.id}>
            <Card title={p.label} extra={<Tag>{p.stream_enabled ? '流式' : '非流式'}</Tag>}>
              <p>{p.review_model}</p>
              <p className="muted">{p.base_url ?? '接口地址保存在配置中'}</p>
              {results[p.id] && <TestResults results={results[p.id].results} />}
              <Space className="section-gap">
                <Button
                  disabled={!editable(state) || test.busy}
                  onClick={() => void testProfile(p.id)}
                >
                  测试连通性
                </Button>
                <Button disabled={!editable(state)} onClick={() => open(p)}>
                  编辑
                </Button>
                <Button danger disabled={!editable(state)} onClick={() => remove(p)}>
                  删除
                </Button>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
      <Modal
        open={editing !== undefined}
        title={editing ? '编辑模型配置' : '新增模型配置'}
        onCancel={() => {
          invalidate();
          setEditing(undefined);
        }}
        destroyOnHidden
        footer={
          <Space>
            <Button
              onClick={() => {
                invalidate();
                setEditing(undefined);
              }}
            >
              取消
            </Button>
            <Button loading={test.busy} disabled={save.busy} onClick={() => void testDraft()}>
              测试连通性
            </Button>
            <Button
              type="primary"
              loading={save.busy}
              disabled={test.busy}
              onClick={() => form.submit()}
            >
              保存配置
            </Button>
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onValuesChange={invalidate}
          onFinish={(values) => void submit(values)}
        >
          <Form.Item name="provider" label="模型厂商" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'deepseek', label: 'DeepSeek' },
                { value: 'qwen', label: 'Qwen' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label="显示名称"
            rules={[{ required: true, whitespace: true, max: 60 }]}
          >
            <Input maxLength={60} />
          </Form.Item>
          <Form.Item name="model" label="模型 ID" rules={[{ required: true, whitespace: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="base_url"
            label="接口根地址（Base URL）"
            rules={[{ required: true }, { type: 'url' }]}
            extra="不包含 /chat/completions"
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key"
            rules={[{ required: !editing }]}
            extra={editing ? '留空保留原密钥' : '保存后不回显'}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="stream" label="使用流式响应" valuePropName="checked">
            <Switch />
          </Form.Item>
          {draftResult && <TestResults results={draftResult.results} />}
          <Alert type="info" message="测试会发送一次小请求，可能产生少量 API 用量。" />
          <ErrorAlert error={save.error} />
          <ErrorAlert error={test.error} />
        </Form>
      </Modal>
    </>
  );
}
