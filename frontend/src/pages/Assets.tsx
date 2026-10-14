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
import { useEffect, useRef, useState } from 'react';
import { api, uploadChunk, uploadFingerprint } from '../api';
import { useWorkbench } from '../context';
import { useAction } from '../hooks/useAction';
import { useConfirmAction } from '../hooks/useConfirmAction';
import { readUploadSession, restoreUpload } from '../uploads';
import type { BatchItem } from '../hooks/useUploadBatch';
import { useUploadBatch } from '../hooks/useUploadBatch';
import { canSubmit, editable, resourceImportTask } from '../presentation';
import { ErrorAlert, Heading, Status } from '../components/Common';
import type { Dataset, ImportTask, Repository, UploadSession } from '../types';
function validUploadFiles(files: UploadFile[], limit: number) {
  return files.length > 0 && files.every((file) => file.name.toLowerCase().endsWith('.zip') && (file.size ?? 0) <= limit);
}
async function waitForImport(task: ImportTask, update: (task: ImportTask) => void) {
  let current = task;
  while (['queued', 'running'].includes(current.status)) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const reply = await api<{ task: ImportTask | null }>(`/api/cases/imports?id=${task.id}`);
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
    [notice, setNotice] = useState(''),
    [pending, setPending] = useState<UploadSession | null>(null);
  const { busy, error, run } = useAction();
  const { message } = App.useApp();
  const limit = state.case_assets?.upload_max_bytes ?? 2 * 1024 ** 3;
  const disabled = !editable(state) || importBusy || busy;
  const valid = validUploadFiles(files, limit);
  const kind = repository ? 'repository' : 'dataset';
  const storageKey = `bench.upload.session.v1.${kind}`;
  const batch = useUploadBatch(kind, task);
  const updateBatch = batch.update;
  useEffect(() => {
    let alive = true;
    void restoreUpload(kind, storageKey)
      .then((upload) => { if (alive) setPending(upload); })
      .catch(() => { if (alive) setNotice('无法读取服务端上传进度，请检查连接后重试'); });
    return () => { alive = false; };
  }, [kind, storageKey]);
  useEffect(() => {
    if (!pending?.task) return;
    const linked = pending.task;
    updateBatch({ name: pending.filename, size: pending.size }, { taskId: linked.id, status: 'importing' });
    setTask(linked);
    localStorage.removeItem(storageKey);
    setPending(null);
  }, [pending, updateBatch, setTask, storageKey]);
  const persist = (upload: UploadSession | null) => {
    if (upload) localStorage.setItem(storageKey, JSON.stringify(upload));
    else localStorage.removeItem(storageKey);
    setPending(upload);
  };
  const finishImport = async (session: UploadSession, file: File) => {
    const task = session.task ?? (await api<{ task?: ImportTask }>(`/api/uploads/${session.id}/complete`, undefined, 'POST')).task;
    persist(null);
    if (task) {
      batch.update(file, { status: 'importing', taskId: task.id });
      setTask(task);
      await waitForImport(task, setTask);
    }
    batch.update(file, { status: 'succeeded' });
    await refresh();
  };
  const startOrResume = async (file: File) => {
    const fingerprint = await uploadFingerprint(file);
    let session = await restoreUpload(kind, storageKey);
    if (session) {
      if (session.filename !== file.name || session.size !== file.size || session.fingerprint !== fingerprint)
        throw new Error(`请先选择未完成上传的同一文件：${session.filename}`);
    } else {
      session = (await api<{ upload: UploadSession }>('/api/uploads/start', {
        filename: file.name, size: file.size, fingerprint, kind,
      })).upload;
    }
    persist(session);
    if (session.status === 'queued' && session.task) return finishImport(session, file);
    if (session.status !== 'receiving') throw new Error('上传会话已结束，请重新选择文件开始上传');
    let offset = session.offset;
    while (offset < file.size) {
      const index = Math.floor(offset / session.chunk_size);
      const chunk = file.slice(offset, Math.min(file.size, offset + session.chunk_size));
      await uploadChunk(session.id, index, chunk, (sent) =>
        setPercent(Math.floor(((offset + sent) / file.size) * 100)),
      );
      offset += chunk.size;
      session = { ...session, offset };
      persist(session);
    }
    await finishImport(session, file);
  };
  const discardPending = async () => {
    if (pending) await api(`/api/uploads/${pending.id}/abort`, undefined, 'POST');
    persist(null);
    batch.clear();
    setFiles([]);
    setNotice('已结束本批次，已导入的仓库不受影响');
  };
  const execute = () =>
    run(async () => {
      const selected = batch.prepare(files.flatMap((entry) => (entry.originFileObj ? [entry.originFileObj] : [])));
      const saved = readUploadSession(storageKey);
      if (saved) {
        const match = selected.findIndex((file) => file.name === saved.filename && file.size === saved.size);
        if (match < 0) throw new Error(`请先选择未完成上传的同一文件：${saved.filename}`);
        selected.unshift(...selected.splice(match, 1));
      }
      for (let index = 0; index < selected.length; index++) {
        const file = selected[index];
        setPercent(0);
        setNotice(`第 ${index + 1}/${selected.length} 个：${file.name}`);
        batch.update(file, { status: 'uploading', taskId: undefined });
        try { await startOrResume(file); }
        catch (failure) { batch.update(file, { status: 'failed' }); throw failure; }
        setFiles(selected.slice(index + 1).map((next) => files.find((entry) => entry.originFileObj === next)).filter((entry): entry is UploadFile => !!entry));
        setNotice(`第 ${index + 1}/${selected.length} 个：已导入 ${file.name}`);
      }
      setFiles([]);
      setNotice('所选文件导入完成；剩余清单见下方');
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
        disabled={pickerBlocked(editable(state), false, busy)}
        files={files}
        limitLabel={state.case_assets.upload_max_label ?? '2 GiB'}
        onChange={(list) => {
          setFiles(list);
          setNotice('');
        }}
      />
      <ImportFeedback
        task={visibleImportTask(task, repository, batch.items.map((item) => item.taskId), busy, pending)}
        notice={notice}
        busy={busy}
        percent={percent}
        importBusy={importBusy}
        error={error}
      />
      <BatchFeedback items={batch.items} busy={pickerBlocked(true, importBusy, busy)} onClear={() => void run(discardPending)} />
      {pending && (
        <Alert
          className="section-gap"
          type="warning"
          showIcon
          message={`发现未完成上传：${pending.filename}`}
          description={`服务端已保存 ${(pending.offset / 1024 / 1024).toFixed(1)} MiB / ${(pending.size / 1024 / 1024).toFixed(1)} MiB。重新选择同一个 ZIP 后继续，只会重传当前未完成分块。`}
          action={<Button size="small" disabled={busy} onClick={() => void run(discardPending)}>放弃断点</Button>}
        />
      )}
      <Typography.Paragraph type="secondary">
        {uploadHint(importBusy, files.length)}
      </Typography.Paragraph>
      <Button
        type="primary"
        loading={busy}
        disabled={disabled || !valid}
        onClick={() => void execute()}
      >
        {uploadLabel(Boolean(pending), repository)}
      </Button>
    </Card>
  );
}
function pickerBlocked(allowed: boolean, active: boolean, busy: boolean) { return !allowed || active || busy; }
function uploadLabel(pending: boolean, repository: boolean) {
  if (pending) return '继续上传并导入';
  return repository ? '导入仓库' : '确认导入';
}
function visibleImportTask(task: ImportTask | null, repository: boolean, ids: (string | undefined)[], busy: boolean, pending: UploadSession | null) {
  const relevant = resourceImportTask(task, repository);
  if (!relevant) return null;
  if (ids.length) return ids.includes(relevant.id) ? relevant : null;
  return busy || pending ? null : relevant;
}
function uploadHint(importing: boolean, selected: number) {
  if (importing) return '后端仍在导入资源，可先选择剩余 ZIP，导入结束后继续。';
  if (!selected) return '请点击上方选择 ZIP；刷新后需要重新选择尚未完成的文件。';
  return '按清单顺序导入，已保存的上传分块会复用。';
}
function BatchFeedback({ items, busy, onClear }: { items: BatchItem[]; busy: boolean; onClear: () => void }) {
  if (!items.length) return null;
  const done = items.filter((item) => item.status === 'succeeded').length;
  const labels = { waiting: '等待选择文件', uploading: '上传未完成，请重新选择', importing: '后端导入中', succeeded: '已导入', failed: '未完成，可重试' };
  return <Space direction="vertical" className="full-width section-gap">
    <Alert type={done === items.length ? 'success' : 'info'} showIcon
      message={`本批次已导入 ${done} / ${items.length} 个 ZIP`}
      description="刷新不会丢失清单。请重新选择未完成的 ZIP，已导入的文件无需重传。"
      action={<Button size="small" disabled={busy} onClick={onClear}>结束本批次</Button>} />
    <Table size="small" pagination={false} dataSource={items} rowKey={(item) => `${item.name}:${item.size}`}
      columns={[{ title: '文件', dataIndex: 'name' }, { title: '状态', render: (_, item: BatchItem) => labels[item.status] }]} />
  </Space>;
}
export function Assets({ repository = false }: { repository?: boolean }) {
  const { state, importBusy, refresh, newRun } = useWorkbench();
  const { message } = App.useApp();
  const confirm = useConfirmAction();
  const [editing, setEditing] = useState<Dataset>(),
    [form] = Form.useForm<{ name: string }>();
  const { run, busy, error, clear } = useAction();
  const uploadRef = useRef<HTMLDivElement>(null);
  const disabled = !editable(state) || importBusy;
  const mutate = async (path: string, data: unknown) => {
    const reply = await api<{ message: string }>(path, data);
    await refresh();
    message.success(reply.message);
  };
  const remove = (id: string, name: string) => {
    const description = repository
      ? '将删除该仓库及已移除案例集引用的仓库资源。历史批次和已保存结果仍可查看，但依赖这些资源的批次不能续跑检视。'
      : '将清理案例清单。历史批次、日志和已保存结果仍可查看；依赖它的批次不能续跑检视或重新评分。仓库资源会保留在仓库管理中，供其他案例集复用。';
    confirm({
      title: `确认删除“${name}”？`, content: description,
      action: () => mutate(
        repository ? '/api/repositories/manage' : '/api/cases/manage',
        repository ? { action: 'delete', slug: id } : { action: 'delete', id },
      ),
    });
  };
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
