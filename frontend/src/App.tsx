import { Alert, App as AntApp, Button, Layout, Menu, Space, Spin, Switch, Tag, Typography } from 'antd';
import {
  DatabaseOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FolderOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { lazy, Suspense, useState } from 'react';
import { Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { WorkbenchContext } from './context';
import { useAppearance } from './theme';
import { useDashboard } from './hooks/useDashboard';
import { useAction } from './hooks/useAction';
import { api } from './api';
import { Login } from './components/Login';
import { ErrorAlert } from './components/Common';
import type { Snapshot } from './types';
const Assets = lazy(() => import('./pages/Assets').then((module) => ({ default: module.Assets })));
const Models = lazy(() => import('./pages/Models').then((module) => ({ default: module.Models })));
const Runs = lazy(() => import('./pages/Runs').then((module) => ({ default: module.Runs })));
const Jobs = lazy(() => import('./pages/Runs').then((module) => ({ default: module.Jobs })));
const Experiments = lazy(() =>
  import('./pages/Experiments').then((module) => ({ default: module.Experiments })),
);
const ExperimentDetail = lazy(() =>
  import('./pages/Experiments').then((module) => ({ default: module.ExperimentDetail })),
);
const Guide = lazy(() => import('./pages/Guide').then((module) => ({ default: module.Guide })));
const CreateRun = lazy(() =>
  import('./components/CreateRun').then((module) => ({ default: module.CreateRun })),
);
const RunDetail = lazy(() =>
  import('./components/RunDetail').then((module) => ({ default: module.RunDetail })),
);
const navigation = [
  { key: '/', label: '单模型测评', icon: <FileTextOutlined /> },
  { key: '/jobs', label: '执行记录', icon: <UnorderedListOutlined /> },
  { key: '/experiments', label: '多模型实验', icon: <ExperimentOutlined /> },
  { key: '/comparisons', label: '结果对比', icon: <DatabaseOutlined /> },
  { key: '/cases', label: '案例集管理', icon: <FolderOutlined /> },
  { key: '/repositories', label: '仓库管理', icon: <DatabaseOutlined /> },
  { key: '/models', label: '模型配置', icon: <SettingOutlined /> },
  { key: '/guide', label: '使用指南', icon: <QuestionCircleOutlined /> },
];
function RuntimeNote({ state }: { state: Snapshot }) {
  if (!state.queue_mode)
    return (
      <Typography.Text type="secondary">本地运行 · 数据与日志保存在当前项目。</Typography.Text>
    );
  if (state.storage_mode === 'ephemeral')
    return <Alert type="warning" message="试运行 · 容器重建后数据丢失，请及时导出。" />;
  const storage = state.run_storage;
  if (storage?.status === 'error')
    return <Alert type="error" message={storage.message ?? 'COS 同步异常，请勿重建容器。'} />;
  if (storage?.status === 'warning')
    return <Alert type="warning" message={storage.message ?? '部分会话检查点未备份，请查看服务日志。'} />;
  if (storage?.status === 'pending')
    return <Alert type="warning" message="等待保存到 COS，请暂缓更新服务。" />;
  return <Typography.Text type="secondary">运行记录、任务日志与 LCR 会话自动备份到 COS。最近同步：{storage?.last_sync_at ? new Date(storage.last_sync_at).toLocaleString('zh-CN', { hour12: false }) : '尚无成功记录'}。</Typography.Text>;
}
function RuntimeAlerts({ state, refresh }: { state: Snapshot; refresh: () => Promise<void> }) {
  const action = useAction(),
    { modal } = AntApp.useApp();
  const recover = () =>
    modal.confirm({
      title: '确认旧任务已经停止？',
      content: '确认后仅解除队列暂停，不会继续已中断批次。要续跑，请打开原批次点击“继续未完成案例并评分”。请先确认旧任务已停止。',
      okText: '解除队列暂停',
      cancelText: '取消',
      onOk: () =>
        action.run(async () => {
          await api('/api/recovery', { confirmed: true });
          await refresh();
        }),
    });
  return (
    <>
      <ErrorAlert error={action.error} />
      {state.queue_mode && !state.worker_ready && (
        <Alert
          className="section-gap"
          type="warning"
          message={
            state.recovery_required
              ? 'Worker 曾中断，队列已暂停。打开中断批次，点击“继续未完成案例并评分”；仅解除暂停不会续跑。'
              : 'Worker 尚未就绪，任务保留在队列中。'
          }
          action={
            state.recovery_required &&
            state.access_role !== 'viewer' && (
              <Button loading={action.busy} onClick={recover}>
                解除队列暂停
              </Button>
            )
          }
        />
      )}
    </>
  );
}
export default function App() {
  const { dark, setDark } = useAppearance();
  const menuTheme = dark ? 'dark' : 'light';
  const dashboard = useDashboard(),
    { state, error, refresh } = dashboard;
  const location = useLocation(),
    navigate = useNavigate(),
    [params, setParams] = useSearchParams();
  const [selection, setSelection] = useState<{ suite?: string; dataset?: string }>();
  const path = location.pathname.replace(/\/$/, '') || '/';
  const title = navigation.find((n) => n.key === path)?.label ?? '测评工作台';
  const openRun = (key: string, job?: string) =>
    setParams({ run: key, tab: job ? 'log' : 'overview', ...(job ? { job } : {}) });
  const openExperiment = (key: string, results = false) =>
    navigate({
      pathname: results ? '/comparisons' : '/experiments',
      search: '?' + new URLSearchParams({ experiment: key }),
    });
  const evidence = (group: string, caseId: string) =>
    setParams({ experiment: params.get('experiment') ?? '', group, case: caseId });
  const closeEvidence = () => setParams({ experiment: params.get('experiment') ?? '' });
  const changeTab = (tab: string, job?: string) => {
    const next = new URLSearchParams(params);
    next.set('tab', tab);
    if (job) next.set('job', job);
    setParams(next, { replace: true });
  };
  if (dashboard.loginRequired) return <Login login={dashboard.login} error={error} />;
  return (
    <Layout className="app-layout">
      <Layout.Sider width={232} breakpoint="lg" collapsedWidth={0} theme={menuTheme} className="sidebar">
        <div className="brand">
          <span className="brand-mark">RL</span>
          <div>
            Review Lab<small>代码检视测评</small>
          </div>
        </div>
        <Typography.Text type="secondary" className="sidebar-caption">WORKSPACE</Typography.Text>
        <Menu
          mode="inline"
          theme={menuTheme}
          selectedKeys={[path]}
          items={navigation}
          onClick={(item) => navigate(item.key)}
        />
        <div className="runtime-note">{state && <RuntimeNote state={state} />}</div>
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Typography.Text>工作空间 / {title}</Typography.Text>
          <Space>
            <Space size="small">
              <Typography.Text>暗色</Typography.Text>
              <Switch aria-label="暗色主题" checked={dark} onChange={setDark} />
            </Space>
            <Tag color={connectionColor(error, Boolean(state))}>
              {connectionLabel(error, Boolean(state))}
            </Tag>
            <Button aria-label="刷新" icon={<ReloadOutlined />} onClick={() => void refresh()} />
          </Space>
        </Layout.Header>
        <Layout.Content className="workspace">
          <ErrorAlert error={error} />
          {state ? (
            <WorkbenchContext.Provider
              value={{
                ...dashboard,
                state,
                newRun: (suite, dataset) => setSelection({ suite, dataset }),
                openRun,
                openExperiment,
              }}
            >
              <RuntimeAlerts state={state} refresh={refresh} />
              <Suspense fallback={<Spin />}>
                <Routes>
                  <Route path="/" element={<Runs />} />
                  <Route path="/jobs" element={<Jobs />} />
                  <Route path="/cases" element={<Assets key="cases" />} />
                  <Route path="/repositories" element={<Assets key="repositories" repository />} />
                  <Route path="/models" element={<Models />} />
                  <Route path="/experiments" element={<Experiments />} />
                  <Route path="/comparisons" element={<Experiments results />} />
                  <Route path="/guide" element={<Guide />} />
                  <Route
                    path="*"
                    element={
                      <Alert
                        message="页面不存在"
                        action={<Button onClick={() => navigate('/')}>返回工作台</Button>}
                      />
                    }
                  />
                </Routes>
                <CreateRun selection={selection} close={() => setSelection(undefined)} />
                {params.has('run') && (
                  <RunDetail
                    key={params.get('run')}
                    runKey={params.get('run')!}
                    jobId={params.get('job') ?? undefined}
                    currentTab={detailTab(params)}
                    onTab={changeTab}
                    close={() => setParams({})}
                  />
                )}
                {params.has('experiment') && (
                  <ExperimentDetail
                    key={params.get('experiment')}
                    experimentKey={params.get('experiment')!}
                    results={path === '/comparisons'}
                    group={params.get('group') ?? undefined}
                    caseId={params.get('case') ?? undefined}
                    showEvidence={evidence}
                    closeEvidence={closeEvidence}
                    close={() => setParams({})}
                  />
                )}
              </Suspense>
            </WorkbenchContext.Provider>
          ) : (
            <div className="loading">
              <Spin size="large" tip="正在读取工作台…" />
            </div>
          )}
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

function detailTab(params: URLSearchParams) {
  const value = params.get('tab') ?? 'overview';
  return ['overview', 'log', 'report', 'files'].includes(value) ? value : 'overview';
}

function connectionLabel(error: string, connected: boolean) {
  if (error) return '连接中断';
  return connected ? '服务已连接' : '正在连接';
}

function connectionColor(error: string, connected: boolean) {
  if (error) return 'error';
  return connected ? 'success' : 'default';
}
