import { useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Select, Space, Table, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { Heading, ErrorAlert } from '../components/Common';
import { bytes, exportInventory, scopes } from '../features/cos/inventory';
import type { CosObject } from '../features/cos/inventory';
import { Cleanup } from '../features/cos/Cleanup';
import { useInventory } from '../features/cos/useInventory';
const columns = [
  { title: '对象路径', dataIndex: 'key', width: 480, render: (key: string) => <Typography.Text copyable style={{ overflowWrap: 'anywhere' }}>{key}</Typography.Text> },
  { title: '大小', dataIndex: 'size', width: 130, render: bytes, sorter: (a: CosObject, b: CosObject) => a.size - b.size, defaultSortOrder: 'descend' as const },
  { title: '分类', dataIndex: 'category', width: 160 },
  { title: '最后修改', dataIndex: 'modified', width: 190, render: (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未知' },
  { title: '整理建议', dataIndex: 'advice', width: 300 },
];
export function CosStorage() {
  const inventory = useInventory();
  const [scope, setScope] = useState('runs'), [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>();
  const categories = useMemo(() => [...new Set(inventory.items.map((row) => row.category))].map((label) => ({ label, value: label })), [inventory.items]);
  const filtered = useMemo(() => inventory.items.filter((row) => row.key.includes(query) && (!category || row.category === category)), [inventory.items, query, category]);
  const total = inventory.items.reduce((sum, row) => sum + row.size, 0);
  return <>
    <Heading title="COS 空间整理" description="找出占用空间的对象，核对用途后再决定清理。" />
    <Space direction="vertical" size="middle" className="full-width">
      <Cleanup />
      <Alert type="info" showIcon message="下方为只读盘点" description="扫描配置前缀内的当前对象，不含历史版本、删除标记和未完成分片，不代表整个桶的计费容量。运行期间对象可能变化，分类建议不等于已确认可删除。" />
      <Space wrap>
        <Select aria-label="扫描范围" value={scope} options={scopes} onChange={setScope} disabled={inventory.busy} style={{ width: 240 }} />
        <Button type="primary" loading={inventory.busy} onClick={() => { setCategory(undefined); void inventory.scan(scope); }}>开始扫描</Button>
        {inventory.busy && <Button onClick={inventory.stop}>停止扫描</Button>}
        <Button disabled={!inventory.items.length} onClick={() => exportInventory(inventory.items, inventory.complete, inventory.prefix)}>导出已扫描清单</Button>
        <Link to="/cases">案例集与仓库管理</Link>
      </Space>
      <ErrorAlert error={inventory.error} />
      <Typography.Text>{inventory.busy ? '扫描中' : inventory.complete ? '本次扫描结束' : '尚无完整扫描'} · 已读取 {inventory.items.length.toLocaleString()} 个对象 · 合计 {bytes(total)}</Typography.Text>
      {inventory.prefix && <Typography.Text type="secondary">本次范围：{inventory.prefix}</Typography.Text>}
      <Space wrap>
        <Input aria-label="对象路径筛选" placeholder="按路径或文件名筛选" allowClear value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 280 }} />
        <Select aria-label="对象分类筛选" placeholder="全部分类" allowClear value={category} onChange={setCategory} options={categories} style={{ width: 200 }} />
        <Typography.Text type="secondary">筛选结果 {filtered.length} 个 · {bytes(filtered.reduce((sum, row) => sum + row.size, 0))}</Typography.Text>
      </Space>
      <Table rowKey="key" columns={columns} dataSource={filtered} scroll={{ x: 1260 }} pagination={{ defaultPageSize: 25, showSizeChanger: true }} locale={{ emptyText: <Empty description={inventory.complete ? '此范围内没有匹配对象' : '选择范围并开始扫描；扫描结果仅保留在本页面'} /> }} />
      <Alert type="warning" showIcon message="清理前需核对引用" description="集群证据以共享数据库为准，旧版运行快照以 manifest 为准。上传中的文件可能尚未发布引用，不能把暂时未引用的对象直接删除。旧案例包也需确认迁移及恢复需求。清理请使用上方维护流程。" />
    </Space>
  </>;
}
