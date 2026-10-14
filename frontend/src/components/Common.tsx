import { Alert, Space, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import { labels } from '../presentation';
export function Status({ status }: { status: string }) {
  const color = {
    failed: 'error',
    partial: 'warning',
    interrupted: 'warning',
    running: 'processing',
    queued: 'processing',
    reviewing: 'processing',
    scoring: 'processing',
    starting: 'processing',
    cancelling: 'processing',
    unfinished_check_required: 'warning',
    recoverable_check_required: 'warning',
    answer_with_item_failures: 'warning',
    scored: 'success',
    succeeded: 'success',
    complete: 'success',
    partial_scored: 'warning',
  }[status];
  return <Tag color={color}>{labels[status] ?? status}</Tag>;
}
export function Heading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <Typography.Title level={2}>{title}</Typography.Title>
        <Typography.Text type="secondary">{description}</Typography.Text>
      </div>
      {action}
    </div>
  );
}
export function ErrorAlert({ error }: { error?: string }) {
  return error ? <Alert type="error" showIcon message={error} className="section-gap" /> : null;
}
export function TestResults({
  results,
}: {
  results: { role: string; status: string; message?: string; elapsed_seconds: number }[];
}) {
  return (
    <Space direction="vertical" className="full-width">
      {results.map((result, index) => (
        <Alert
          key={index}
          showIcon
          type={result.status === 'passed' ? 'success' : 'warning'}
          message={`${result.role} · ${result.message ?? '连通正常'} · ${result.elapsed_seconds}s`}
        />
      ))}
      <Typography.Text type="secondary">仅验证基础请求，不代表完整测评通过。</Typography.Text>
    </Space>
  );
}
