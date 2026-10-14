import { Alert, Button, Segmented, Select, Space } from 'antd';
import { useState } from 'react';
import { download } from '../api';
import { reportDetails, reportSummary } from '../reportPresentation';
import type { Run, TextReply } from '../types';
import { Markdown } from './Markdown';

export function ReportView({ run, reply, path }: { run: Run; reply?: TextReply; path?: string }) {
  if (!reply || !path) return <Markdown text="还没有生成报告。" />;
  if (path !== 'report.zh-CN.md') return <Markdown text={reply.text} />;
  return <LoadedReport run={run} reply={reply} path={path} />;
}
function LoadedReport({ run, reply, path }: { run: Run; reply: TextReply; path: string }) {
  const [view, setView] = useState('摘要');
  const [selected, setSelected] = useState(0);
  const sections = reportDetails(reply.text);
  const summary = reportSummary(reply.text, run);
  const section = sections[selected] ?? sections[0];
  const fullName = path.split('/').pop() ?? 'report.md';
  return <>
    <Space wrap className="section-gap">
      <Segmented value={view} options={['摘要', '案例明细']} onChange={setView} />
      <Button disabled={reply.truncated} onClick={() => download(fullName.replace(/\.md$/, '.summary.md'), summary)}>下载报告摘要</Button>
      <Button disabled={reply.truncated} onClick={() => download(fullName, reply.text)}>下载完整报告</Button>
    </Space>
    {reply.truncated && <Alert type="warning" message="报告超过页面读取上限，当前仅为部分内容，暂不汇总和导出摘要。请在原始文件中下载结果归档获取完整报告。" />}
    {view === '摘要' && !reply.truncated ? <Markdown text={summary} /> : <>
      <Select className="file-select section-gap" aria-label="案例明细章节" value={selected}
        options={sections.map((item, index) => ({ value: index, label: item.title }))}
        onChange={setSelected} placeholder="完整报告" />
      <Markdown text={section?.text ?? reply.text} />
    </>}
  </>;
}
