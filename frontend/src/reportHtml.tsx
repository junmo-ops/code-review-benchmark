import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { reportDetails, reportSections, reportSummary } from './reportPresentation';
import { initOfflineReport } from './reportHtmlController';
import type { Run } from './types';

const styles = `
*{box-sizing:border-box}body{margin:0;background:#f5f5f5;color:#262626;font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
header{background:#fff;border-bottom:1px solid #eee;padding:18px 28px}header strong{font-size:18px}main{max-width:1160px;margin:24px auto;padding:0 20px}
.toolbar{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:20px}.tabs{background:#eaeaea;border-radius:7px;padding:3px;display:flex}
button,select{font:inherit;border:1px solid #d9d9d9;background:white;border-radius:6px;padding:6px 16px;color:#262626}button{cursor:pointer}button:hover{color:#1677ff;border-color:#1677ff}
.tabs button{border:0;background:transparent}.tabs button[aria-pressed=true]{background:white;box-shadow:0 1px 3px #0002}
.card{background:white;border:1px solid #eee;border-radius:8px;padding:22px 24px;margin-bottom:18px}.muted{color:#8c8c8c}h1{font-size:24px}h2{font-size:18px}h3{font-size:16px}h1,h2,h3{margin-top:0}
.markdown{overflow-wrap:anywhere}.markdown table{border-collapse:collapse;display:block;max-width:100%;overflow:auto;font-size:13px}.markdown th{text-align:left;background:#fafafa;font-weight:600}
.markdown th,.markdown td{padding:10px 14px;border:1px solid #eee}.markdown blockquote{margin:12px 0;background:#fffbe6;border-left:3px solid #ffe58f;padding:10px 16px}.markdown blockquote p{margin:0}
.markdown pre{padding:14px;background:#fafafa;border:1px solid #eee;border-radius:6px;overflow:auto}.markdown code{font-family:ui-monospace,monospace}.markdown a{color:#1677ff}select{width:100%;margin-bottom:20px}
footer{text-align:center;color:#999;margin:24px}[hidden]{display:none!important}@media(max-width:700px){main{padding:0 12px}.card{padding:16px}.toolbar{display:block}.tabs{margin-bottom:12px}}
@media print{.toolbar,select,footer{display:none}body{background:white}main{max-width:none;margin:0}.card{break-inside:avoid}}
`;

function OfflineMarkdown({ text }: { text: string }) {
  return <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => /^https?:\/\//i.test(href ?? '')
      ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
      : <span title="原始证据文件请从结果归档获取">{children}</span>,
    img: ({ alt }) => <span>{alt ? `[图片：${alt}]` : '[图片未嵌入离线报告]'}</span>,
  }}>{text}</ReactMarkdown></article>;
}

export function buildReportHtml(text: string, run: Run) {
  const sections = reportDetails(text);
  const cards = reportSections(reportSummary(text, run));
  const title = `${run.display_name ?? run.name} · 测评报告`;
  // All source content is data; prevent a finding from closing this script element.
  const payload = JSON.stringify({ text }).replaceAll('<', '\\u003c');
  return '<!doctype html>\n' + renderToStaticMarkup(<html lang="zh-CN">
    <head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
      <meta httpEquiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'" />
      <title>{title}</title><style>{styles}</style></head>
    <body><header><strong>Review Lab / 中文报告</strong><div className="muted">{title} · 离线报告</div></header>
      <main><div className="toolbar"><div className="tabs" role="group" aria-label="报告视图">
        <button type="button" data-report-view="summary" aria-pressed="true">报告摘要</button>
        <button type="button" data-report-view="details" aria-pressed="false">案例明细</button>
      </div><button type="button" id="download-markdown">下载原始 Markdown</button></div>
        <noscript>交互切换需要启用浏览器 JavaScript；可查看下方摘要。</noscript>
        <section id="report-summary">{cards.map((card, index) => <div className="card" key={index}><OfflineMarkdown text={card.text} /></div>)}</section>
        <section id="report-details" hidden><div className="card"><h2>案例明细</h2>
          <label htmlFor="report-section">选择章节、案例或评分轮次</label>
          <select id="report-section" defaultValue="0">{sections.map((section, index) => <option value={index} key={index}>{section.title}</option>)}</select>
          {sections.map((section, index) => <div key={index} data-report-section={index} hidden={index !== 0}><OfflineMarkdown text={section.text} /></div>)}
          {!sections.length && <OfflineMarkdown text={text} />}
        </div></section>
        <footer>导出时的批次快照 · 无需联网，不触发模型调用 · 原始证据文件请从结果归档获取</footer>
      </main>
      <script id="report-source" type="application/json" dangerouslySetInnerHTML={{ __html: payload }} />
      <script dangerouslySetInnerHTML={{ __html: `(${initOfflineReport.toString()})();` }} />
    </body></html>);
}
