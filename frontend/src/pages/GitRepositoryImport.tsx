import { useState } from 'react';
import { Alert, Button, Card, Input, Space, Typography } from 'antd';
import { api } from '../api';
import { useWorkbench } from '../context';
import { useAction } from '../hooks/useAction';
import { ErrorAlert, Status } from '../components/Common';
import { editable } from '../presentation';
import type { ImportTask } from '../types';

export function GitRepositoryImport() {
  const { state, task, setTask, importBusy } = useWorkbench();
  const action = useAction();
  const [url, setUrl] = useState('');
  const submit = () => action.run(async () => {
    const result = await api<{ task: ImportTask }>('/api/repositories/git', { url: url.trim() });
    setTask(result.task);
  });
  return <Card title="从行内 Git 导入 / 更新仓库" className="section-gap">
    <Space direction="vertical" className="full-width">
      <Typography.Text>填写无需鉴权的 HTTP(S) Git 地址。首次完整 clone，再次提交相同地址会 fetch 更新历史。</Typography.Text>
      <Input aria-label="Git 仓库地址" value={url} onChange={(event) => setUrl(event.target.value)}
        placeholder="https://gitee-proxy.paasst.cmbchina.cn/S990768/LV01.30_Checkmate.git" />
      <Button type="primary" loading={action.busy} disabled={!url.trim() || importBusy || !editable(state)} onClick={() => void submit()}>导入 / 更新</Button>
      <ErrorAlert error={action.error} />
      {task && <Alert type={task.status === 'failed' ? 'error' : 'info'} message={<Space><Status status={task.status} />{task.message}</Space>} />}
      <Typography.Text type="secondary">刷新后可继续查看进度。远端需保留 bench_preserved 历史分支；案例中的仓库名须与 Git 地址里的组织/项目名一致。</Typography.Text>
    </Space>
  </Card>;
}
