import { useState } from 'react';
import { Alert, App, Button, Space, Table, Typography } from 'antd';
import { api } from '../../api';
import { useAction } from '../../hooks/useAction';
import { ErrorAlert } from '../../components/Common';
import { bytes } from './inventory';
interface Maintenance { active: boolean; ready: boolean; writers: number; tasks: number }
interface Preview { id: string; items: { key: string; size: number }[]; bytes: number; prefix: string }
interface Result { deleted: string[]; failed: string[]; remaining: number; bytes: number }
export function Cleanup() {
  const action = useAction(), { modal } = App.useApp();
  const [state, setState] = useState<Maintenance>();
  const [preview, setPreview] = useState<Preview>();
  const [result, setResult] = useState<Result>();
  const refresh = () => action.run(async () => setState(await api<Maintenance>('/api/cos/maintenance')));
  const maintain = (active: boolean) => action.run(async () => {
    setState(await api<Maintenance>('/api/cos/maintenance', { active })); setPreview(undefined); setResult(undefined);
  });
  const scan = () => action.run(async () => {
    setPreview(undefined); setResult(undefined);
    setPreview(await api<Preview>('/api/cos/cleanup/scan', {}));
  });
  const remove = () => {
    if (!preview) return;
    modal.confirm({ title: `确认删除 ${preview.items.length} 个未引用对象？`,
      content: `预计释放 ${bytes(preview.bytes)}。删除不可撤销；服务端将再次核对引用和对象内容标识。`,
      okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: () => action.run(async () => {
        const value = await api<Result>('/api/cos/cleanup/delete', { id: preview.id, confirmed: true });
        setResult(value); setPreview(undefined);
      }),
    });
  };
  return <Space direction="vertical" className="full-width" size="middle">
    <Typography.Title level={3}>未引用文件清理</Typography.Title>
    <Alert type="warning" showIcon message="所有 Web / Worker 必须先升级到支持维护状态的同一版本" description="仅清理当前集群 scope 的执行对象；保留数据库任务、历史尝试、检查点、资源目录与上传记录递归引用的对象，以及最近 24 小时对象。旧版快照、案例包、其他 scope 和模型配置不参与删除。启用过版本控制的桶不支持此清理。" />
    <Space wrap>
      <Button loading={action.busy} onClick={() => void refresh()}>刷新维护状态</Button>
      <Button disabled={action.busy || state?.active} onClick={() => modal.confirm({ title: '进入 COS 维护状态？', content: '暂停领取新任务并禁止新上传及修改。现有任务继续完成；需主动退出才恢复。', onOk: () => maintain(true) })}>进入维护状态</Button>
      <Button disabled={action.busy || !state?.active} onClick={() => void maintain(false)}>退出维护状态</Button>
      <Button type="primary" loading={action.busy} disabled={!state?.ready} onClick={() => void scan()}>识别未引用对象</Button>
    </Space>
    {state && <Typography.Text>{state.active ? '维护中' : '正常运行'} · 在途操作 {state.writers} · 运行任务 {state.tasks}。{state.ready ? '可以扫描。' : '进入维护后，请等待任务结束并刷新状态。'}</Typography.Text>}
    <ErrorAlert error={action.error} />
    {preview && <>
      <Typography.Text strong>候选 {preview.items.length} 个 · 预计释放 {bytes(preview.bytes)}</Typography.Text>
      <Typography.Text type="secondary">{preview.prefix} · 每次最多展示并清理 1000 个，预览 15 分钟有效。</Typography.Text>
      <Table size="small" rowKey="key" dataSource={preview.items} pagination={{ pageSize: 10 }} scroll={{ x: 650 }} columns={[
        { title: '未引用对象', dataIndex: 'key', render: (key: string) => <Typography.Text style={{ overflowWrap: 'anywhere' }}>{key}</Typography.Text> },
        { title: '大小', dataIndex: 'size', render: bytes, width: 140 },
      ]} />
      <Button danger disabled={action.busy || !preview.items.length} onClick={remove}>确认清理这些对象</Button>
    </>}
    {result && <Alert type={result.failed.length ? 'warning' : 'success'} message={`已删除 ${result.deleted.length} 个，按对象大小合计 ${bytes(result.bytes)}；剩余 ${result.remaining} 个`} description="未自动退出维护状态。删除失败时停止后续删除；修复权限或存储问题后重新扫描。实际容量变化以 COS 控制台为准。" />}
  </Space>;
}
