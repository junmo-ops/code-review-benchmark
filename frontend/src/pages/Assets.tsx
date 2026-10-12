import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { useRef, useState } from 'react';
import { api, upload } from '../api';
import { useWorkbench } from '../context';
import { useAction } from '../hooks/useAction';
import { canSubmit, editable } from '../presentation';
import { ErrorAlert, Heading, Status } from '../components/Common';
import type { Dataset, ImportTask, Repository, UploadReply } from '../types';
async function waitForImport(task: ImportTask, update: (task: ImportTask) => void) {
  let current = task;
  while (['queued', 'running'].includes(current.status)) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const reply = await api<{ task: ImportTask | null }>('/api/cases/imports');
    if (!reply.task || reply.task.id !== task.id) throw new Error('导入任务已变化，请检查导入状态');
    current = reply.task;
    update(current);
  }
  if (current.status !== 'succeeded')
    throw new Error(current.message || '导入失败，请查看服务日志');
}
function ResourceUpload({ repository }: { repository: boolean }) {
  const { state, task, importBusy, setTask, refresh } = useWorkbench();
  const [files, setFiles] = useState<UploadFile[]>([]),
    [percent, setPercent] = useState(0),
    [notice, setNotice] = useState('');
  const { busy, error, run } = useAction();
  const { message } = App.useApp();
  const limit = state.case_assets?.upload_max_bytes ?? 2 * 1024 ** 3;
  const disabled = !editable(state) || !!state.active || importBusy || busy;
  const valid =
    files.length > 0 &&
    files.every((f) => f.name.toLowerCase().endsWith('.zip') && (f.size ?? 0) <= limit);
  const execute = () =>
    run(async () => {
      for (let index = 0; index < files.length; index++) {
        const file = files[index].originFileObj;
        if (!file) throw new Error('文件不可用，请重新选择');
        setPercent(0);
        setNotice(`第 ${index + 1}/${files.length} 个：${file.name}`);
        const result = await upload<UploadReply>(
          repository ? '/api/repositories/upload' : '/api/cases/upload',
          file,
          setPercent,
        );
        setFiles(files.slice(index + 1));
        if (result.task) {
          setTask(result.task);
          setNotice(`第 ${index + 1}/${files.length} 个：等待后台导入 ${file.name}`);
          await waitForImport(result.task, setTask);
        }
        await refresh();
      }
      setFiles([]);
      setNotice('全部导入完成');
      message.success('资源已导入');
    });
  if (!state.case_assets || !editable(state))
    return <Alert message="当前工作台未开启资源管理，或账号为只读权限。" type="info" showIcon />;
  return (
    <Card
      title={repository ? '导入 / 更新仓库' : '导入案例集'}
      extra={<Tag>不会调用模型</Tag>}
      className="section-gap"
    >
      <UploadPicker
        repository={repository}
        disabled={disabled}
        files={files}
        limitLabel={state.case_assets.upload_max_label ?? '2 GiB'}
        onChange={(list) => {
          setFiles(list);
          setNotice('');
        }}
      />
      <ImportFeedback
        task={task}
        notice={notice}
        busy={busy}
        percent={percent}
        importBusy={importBusy}
        error={error}
      />
      <Button
        type="primary"
        loading={busy}
        disabled={disabled || !valid}
        onClick={() => void execute()}
      >
        {repository ? '导入仓库' : '确认导入'}
      </Button>
    </Card>
  );
}
export function Assets({ repository = false }: { repository?: boolean }) {
  const { state, importBusy, refresh, newRun } = useWorkbench();
  const { modal, message } = App.useApp();
  const [editing, setEditing] = useState<Dataset>(),
    [form] = Form.useForm<{ name: string }>();
  const { run, busy, error, clear } = useAction();
  const uploadRef = useRef<HTMLDivElement>(null);
  const disabled = !editable(state) || !!state.active || importBusy;
  const mutate = async (path: string, data: unknown) => {
    const reply = await api<{ message: string }>(path, data);
    await refresh();
    message.success(reply.message);
  };
  const remove = (id: string, name: string) =>
    modal.confirm({
      title: `确认删除“${name}”？`,
      content: '被案例集或已创建批次引用的资源不能删除。',
      okText: '确认删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () =>
        mutate(
          repository ? '/api/repositories/manage' : '/api/cases/manage',
          repository ? { action: 'delete', slug: id } : { action: 'delete', id },
        ),
    });
  const uploaded = new Map(state.case_assets?.datasets?.map((d) => [d.id, d]));
  const datasets = state.catalog.flatMap((s) =>
    s.datasets.map((d) => ({ ...d, suite: s.name, suiteId: s.id })),
  );
  const caseColumns = [
    {
      title: '案例集名称',
      key: 'name',
      render: (_: unknown, d: Dataset) => (
        <>
          {d.name ?? d.id}
          <div className="muted">{d.id}</div>
        </>
      ),
    },
    { title: '测评类型', dataIndex: 'suite' },
    { title: '数量', dataIndex: 'count' },
    {
      title: '来源',
      key: 'source',
      render: (_: unknown, d: Dataset) => (uploaded.has(d.id) ? '网页导入' : '内置'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, d: (typeof datasets)[number]) => (
        <Space>
          {uploaded.has(d.id) && (
            <>
              <Button
                type="link"
                disabled={disabled}
                onClick={() => {
                  clear();
                  setEditing(d);
                  form.setFieldsValue({ name: d.name ?? '' });
                }}
              >
                编辑
              </Button>
              <Button
                type="link"
                danger
                disabled={disabled}
                onClick={() => remove(d.id, d.name ?? d.id)}
              >
                删除
              </Button>
            </>
          )}
          <Button
            type="link"
            disabled={!canSubmit(state) || importBusy}
            onClick={() => newRun(d.suiteId, d.id)}
          >
            新建测评
          </Button>
        </Space>
      ),
    },
  ];
  const repoColumns = [
    { title: '仓库', dataIndex: 'repo' },
    {
      title: '版本历史',
      key: 'versions',
      render: (_: unknown, r: Repository) => (
        <Space direction="vertical">
          {r.versions.map((v) => (
            <Space key={v.sha256}>
              <Typography.Text code>{v.sha256.slice(0, 12)}</Typography.Text>
              <Tag>{v.current ? '当前' : '历史'}</Tag>
              {(v.size / 1024 ** 2).toFixed(1)} MiB
            </Space>
          ))}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, r: Repository) => (
        <Space>
          <Button
            type="link"
            disabled={disabled}
            onClick={() => uploadRef.current?.scrollIntoView({ behavior: 'smooth' })}
          >
            更新版本
          </Button>
          <Button type="link" danger disabled={disabled} onClick={() => remove(r.slug, r.repo)}>
            删除
          </Button>
        </Space>
      ),
    },
  ];
  return (
    <>
      <Heading
        title={repository ? '仓库管理' : '案例集管理'}
        description={
          repository
            ? '独立维护完整 Git 历史，多个案例集可重复引用。'
            : '维护案例清单；更换案例数量无需重新上传仓库。'
        }
      />
      <div ref={uploadRef}>
        <ResourceUpload key={String(repository)} repository={repository} />
      </div>
      <Card title={repository ? '已登记仓库' : '已登记案例集'}>
        {repository ? (
          <Table
            rowKey="slug"
            columns={repoColumns}
            dataSource={state.case_assets?.repositories ?? []}
            scroll={{ x: 700 }}
          />
        ) : (
          <Table rowKey="id" columns={caseColumns} dataSource={datasets} scroll={{ x: 800 }} />
        )}
      </Card>
      <Modal
        open={!!editing}
        title="修改案例集名称"
        onCancel={() => setEditing(undefined)}
        onOk={() => form.submit()}
        confirmLoading={busy}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) =>
            void run(async () => {
              await mutate('/api/cases/manage', {
                action: 'rename',
                id: editing?.id,
                name: values.name,
              });
              setEditing(undefined);
            })
          }
        >
          <Form.Item
            name="name"
            label="案例集名称"
            rules={[{ required: true, whitespace: true, max: 100 }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <ErrorAlert error={error} />
        </Form>
      </Modal>
    </>
  );
}

function UploadPicker({
  repository,
  disabled,
  files,
  limitLabel,
  onChange,
}: {
  repository: boolean;
  disabled: boolean;
  files: UploadFile[];
  limitLabel: string;
  onChange: (files: UploadFile[]) => void;
}) {
  return (
    <Upload.Dragger
      multiple={repository}
      accept=".zip"
      disabled={disabled}
      fileList={files}
      beforeUpload={() => false}
      onChange={(info) => onChange(info.fileList)}
    >
      <UploadOutlined className="upload-icon" />
      <p>{repository ? '选择或拖入仓库 ZIP，支持批量上传' : '选择或拖入案例配置 ZIP'}</p>
      <Typography.Text type="secondary">
        {repository
          ? '需包含 .git 历史；也支持仓库补充包'
          : '仅需案例清单和仓库引用，已有仓库可复用'}{' '}
        · 单文件上限 {limitLabel}
      </Typography.Text>
    </Upload.Dragger>
  );
}
function ImportFeedback({
  task,
  notice,
  busy,
  percent,
  importBusy,
  error,
}: {
  task: ImportTask | null;
  notice: string;
  busy: boolean;
  percent: number;
  importBusy: boolean;
  error: string;
}) {
  return (
    <Space direction="vertical" className="full-width section-gap">
      {notice && <Alert message={notice} type="info" />}
      {busy && (
        <>
          <Progress percent={percent} status="active" />
          <Typography.Text>
            {percent === 100 ? '已上传，正在整理和校验资源…' : '正在上传…'}
          </Typography.Text>
        </>
      )}
      {task && (
        <Alert
          type={task.status === 'failed' ? 'error' : 'info'}
          message={
            <Space>
              <Status status={task.status} />
              {task.message}
            </Space>
          }
          description={
            task.total > 0 && (
              <Progress
                percent={Math.round((task.completed / task.total) * 100)}
                status={importBusy ? 'active' : 'normal'}
              />
            )
          }
        />
      )}
      <ErrorAlert error={error} />
    </Space>
  );
}
