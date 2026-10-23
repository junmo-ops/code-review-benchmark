import { Alert, Button, Descriptions, Modal, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined, FileSearchOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { downloadResource } from '../api';
import { useWorkbench } from '../context';
import { ErrorAlert, Heading } from '../components/Common';
import { useAction } from '../hooks/useAction';
import { useRemote } from '../hooks/useRemote';
import { ResourceUpload } from './Assets';
import type { SkillFile, SkillListing, SkillVersion } from '../types';

function Hash({ value }: { value: string }) {
  return <Typography.Text code style={{ overflowWrap: 'anywhere' }}>{value}</Typography.Text>;
}

function VersionDetail({ id, close }: { id?: string; close: () => void }) {
  const { data, error } = useRemote<SkillVersion>(id ? `/api/skills/${id}` : undefined);
  const action = useAction();
  return <Modal open={!!id} title="Skill 版本详情" onCancel={close} footer={null} width={900} destroyOnHidden>
    <ErrorAlert error={error ?? action.error} />
    {!data && !error && <Spin />}
    {data && <Space direction="vertical" className="full-width">
      <Descriptions column={1} size="small" bordered items={[
        { key: 'name', label: 'Skill', children: data.name },
        { key: 'description', label: '描述', children: data.description },
        { key: 'main', label: '主说明', children: data.main },
        { key: 'content', label: '内容 SHA-256', children: <Hash value={data.content_sha256} /> },
      ]} />
      <Table<SkillFile> rowKey="path" size="small" dataSource={data.files} scroll={{ x: 600 }} columns={[
        { title: '资源', dataIndex: 'path', render: (path: string) => <span style={{ overflowWrap: 'anywhere' }}>{path}</span> },
        { title: '字节', dataIndex: 'size', width: 90 },
        { title: 'SHA-256', dataIndex: 'sha256', render: (hash: string) => <Hash value={hash} /> },
        { title: '操作', width: 65, render: (_, file) => <Tooltip title="下载资源">
          <Button aria-label={`下载 ${file.path}`} icon={<DownloadOutlined />} loading={action.busy} onClick={() => void action.run(() =>
            downloadResource(`/api/skills/${data.id}/file?path=${encodeURIComponent(file.path)}`, file.path.split('/').pop() ?? 'resource'))} />
        </Tooltip> },
      ]} />
      {data.archives.map((hash, index) => <Space direction="vertical" key={hash} className="full-width">
        <Typography.Text>原包 SHA-256：<Hash value={hash} /></Typography.Text>
        <Button icon={<DownloadOutlined aria-hidden />} loading={action.busy} onClick={() => void action.run(() =>
          downloadResource(`/api/skills/${data.id}/archive?sha256=${hash}`, `${data.name}-${hash.slice(0, 12)}.zip`))}>
          {index === 0 ? '下载原包' : '下载重复导入原包'}
        </Button>
      </Space>)}
    </Space>}
  </Modal>;
}

export function Skills() {
  const { importBusy } = useWorkbench();
  const { data, error } = useRemote<SkillListing>('/api/skills', importBusy ? 0 : 60000);
  const [selected, setSelected] = useState<string>();
  return <>
    <Heading title="Skill 资源管理" description="" />
    <ErrorAlert error={error ?? ''} />
    {!data && !error && <Spin />}
    {data && <>
      {data.supported ? <>
        <ResourceUpload kind="skill" uploadLimits={data.limits} />
        <Typography.Paragraph type="secondary">
          {data.storage_mode === 'cos' ? 'COS 保存确认后可选' : '本地存储'} · 解压上限 {data.limits.unpacked_max_bytes / 1024 ** 2} MiB · 最多 {data.limits.max_entries} 条目
        </Typography.Paragraph>
      </> : <Alert type="warning" showIcon message={data.message} />}
      {data.unavailable.map((item) => <Alert className="section-gap" key={item.id} type="error" showIcon message={item.message} />)}
      <Table<SkillVersion> rowKey="id" dataSource={data.versions} scroll={{ x: 650 }} columns={[
        { title: 'Skill', dataIndex: 'name' },
        { title: '内容版本', dataIndex: 'id', render: (id: string) => <Typography.Text code>{id.slice(0, 12)}</Typography.Text> },
        { title: '资源数', render: (_, item) => item.files.length },
        { title: '导入时间', dataIndex: 'created_at', render: (time: string) => new Date(time).toLocaleString('zh-CN', { hour12: false }) },
        { title: '操作', render: (_, item) => <Button type="link" icon={<FileSearchOutlined aria-hidden />} onClick={() => setSelected(item.id)}>版本详情</Button> },
      ]} />
    </>}
    <VersionDetail key={selected} id={selected} close={() => setSelected(undefined)} />
  </>;
}
