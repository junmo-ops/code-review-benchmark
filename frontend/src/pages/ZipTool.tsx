import { Link } from 'react-router-dom';
import { useState } from 'react';
import { Alert, Button, Card, Input, Popconfirm, Progress, Space, Table, Tag, Typography, Upload } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { api } from '../api';
import { Heading, ErrorAlert } from '../components/Common';
import { useAction } from '../hooks/useAction';
import { useRemote } from '../hooks/useRemote';
import { useWorkbench } from '../context';
import { downloadZipResult, sendZip } from '../features/zipTool/api';
import type { ZipListing, ZipTask } from '../features/zipTool/api';

const states = {
  receiving: ['待续传', 'warning'], queued: ['等待处理', 'default'],
  running: ['处理中', 'processing'], succeeded: ['已完成', 'success'], failed: ['失败', 'error'],
};

function ResultActions({ task, readonly }: { task: ZipTask; readonly: boolean }) {
  const action = useAction();
  const removable = !['queued', 'running'].includes(task.status);
  return <Space direction="vertical">
    <ErrorAlert error={action.error} />
    {task.status === 'succeeded' && <Space wrap>
      <Button type="primary" loading={action.busy} disabled={readonly} onClick={() => void action.run(() => downloadZipResult(task, 'zip'))}>下载 ZIP</Button>
      <Button type="link" disabled={readonly} onClick={() => void action.run(() => downloadZipResult(task, 'instructions'))}>推送说明</Button>
      <Button type="link" disabled={readonly} onClick={() => void action.run(() => downloadZipResult(task, 'manifest'))}>校验清单</Button>
    </Space>}
    {removable && <Popconfirm title="清理此文件和处理记录？" description="清理后无法续传或下载，请先保存需要的结果。" okText="清理" cancelText="取消"
      onConfirm={() => action.run(async () => { await api(`/api/zip-tool/${task.id}`, undefined, 'DELETE'); })}>
      <Button danger size="small" disabled={readonly} loading={action.busy}>清理</Button>
    </Popconfirm>}
  </Space>;
}

export function ZipTool() {
  const { state } = useWorkbench();
  const readonly = state.access_role === 'viewer';
  const { data, error } = useRemote<ZipListing>('/api/zip-tool', 2000);
  const action = useAction();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [main, setMain] = useState('');
  const [progress, setProgress] = useState<{ name: string; percent: number }>();
  const submit = () => action.run(async () => {
    for (const item of files) {
      const file = item.originFileObj;
      if (!file) throw new Error('请重新选择 ZIP 文件');
      setProgress({ name: file.name, percent: 0 });
      await sendZip(file, main, (percent) => setProgress({ name: file.name, percent }));
      setFiles((previous) => previous.filter((entry) => entry.uid !== item.uid));
    }
    setProgress(undefined);
  });
  return <>
    <Link to="/tools">← 返回工具箱</Link>
    <Heading title="仓库 ZIP 工具" description="上传 ZIP → 整理历史 → 下载 ZIP。仅处理文件，不导入测评平台。" />
    <ErrorAlert error={error ?? action.error} />
    <Card title="上传并处理" className="section-gap">
      <Space direction="vertical" className="full-width" size="middle">
        <Typography.Paragraph>保留原提交 SHA，整理为主分支和一条历史保留分支，按实际项目名输出。例如 bluewave-labs.zip → Checkmate.zip。</Typography.Paragraph>
        <Upload.Dragger accept=".zip" multiple maxCount={5} fileList={files} disabled={action.busy || readonly}
          beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖入仓库 ZIP</p>
          <p className="ant-upload-hint">每包包含一个完整 .git 仓库；单包最大 2 GiB，一次最多 5 个，后台逐包处理。</p>
        </Upload.Dragger>
        <Input aria-label="主分支" placeholder="主分支（可选）：默认自动识别；识别失败时填 main、master 或 develop" value={main}
          disabled={action.busy || readonly} onChange={(event) => setMain(event.target.value)} />
        <Button type="primary" loading={action.busy} disabled={!files.length || readonly} onClick={() => void submit()}>上传并处理</Button>
        {progress && <div><Typography.Text>{progress.name} · {action.busy ? '正在上传' : '上传暂停，可重新选择文件续传'}</Typography.Text><Progress percent={progress.percent} /></div>}
        <Alert type="info" showIcon message="处理记录刷新后仍可查看；上传中断时，重新选择同一 ZIP 可续传。"
          description={`文件保留 ${data?.limits.retention_hours ?? 24} 小时，可随时下载后清理。文件仅存于当前服务临时目录，容器重建可能丢失，请及时下载。`} />
      </Space>
    </Card>
    <Table<ZipTask> rowKey="id" dataSource={data?.tasks} loading={!data && !error} scroll={{ x: 950 }} columns={[
      { title: '文件', dataIndex: 'filename', render: (name: string, task) => <Space direction="vertical"><Typography.Text>{name}</Typography.Text>{task.output_file && <Typography.Text type="secondary">→ {task.output_file}</Typography.Text>}</Space> },
      { title: '状态与进度', render: (_, task) => <Space direction="vertical"><Tag color={states[task.status][1]}>{states[task.status][0]}</Tag><Typography.Text>{task.message}</Typography.Text>{task.status === 'receiving' && <Progress size="small" percent={Math.floor(task.offset / task.size * 100)} />}</Space> },
      { title: '处理结果', render: (_, task) => task.status === 'succeeded' ? `${task.commits} 个原提交 · ${task.branches} 个分支` : '—' },
      { title: '保留至', render: (_, task) => new Date(task.expires_at * 1000).toLocaleString('zh-CN', { hour12: false }) },
      { title: '操作', render: (_, task) => <ResultActions task={task} readonly={readonly} /> },
    ]} />
  </>;
}
