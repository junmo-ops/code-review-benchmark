import type { Run } from './types';
import { labels } from './presentation';

export interface ReportSection { title: string; text: string }
// Split only real headings, not headings inside quoted source code.
export function reportSections(text: string): ReportSection[] {
  const sections: ReportSection[] = [{ title: '', text: '' }];
  let fence = '';
  for (const line of text.split('\n')) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = '';
    }
    const heading = !fence && /^## (.+)$/.exec(line);
    if (heading) sections.push({ title: heading[1], text: '' });
    sections[sections.length - 1].text += line + '\n';
  }
  return sections;
}
function numeric(value: string) {
  const cleaned = value.replaceAll(',', '').trim();
  return /^\d+(\.\d+)?$/.test(cleaned) ? Number(cleaned) : undefined;
}
const number = (value: number | undefined) => value === undefined ? '未提供' : value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
const cell = (value: string) => value.replaceAll('|', '\\|').replaceAll('\n', ' ');

function usageSummary(text: string) {
  const rows = text.split('\n').filter((line) => /^\| 检视：/.test(line))
    .map((line) => line.split('|').slice(1, -1).map((part) => part.trim()));
  if (!rows.length) return '## 耗时与 Token\n\n该报告未提供可汇总的检视用量，不能按零计算。\n';
  const totals = [1, 2, 3, 4].map((column) => {
    const values = rows.map((row) => numeric(row[column] ?? '')).filter((v) => v !== undefined);
    return `${number(values.length ? values.reduce((a, b) => a + b, 0) : undefined)}（${values.length}/${rows.length} 例）`;
  });
  const tokens = rows.map((row) => numeric(row[4] ?? '')).filter((v) => v !== undefined);
  const judge = text.split('\n').find((line) => line.startsWith('| 裁判评分 |'));
  return ['## 耗时与 Token', '', '| 阶段 | 累计案例耗时（秒） | 输入 Token | 输出 Token | 总 Token |',
    '|---|---:|---:|---:|---:|', `| 检视已知合计 | ${totals.join(' | ')} |`, judge ?? '', '',
    `有记录案例平均 Token：${number(tokens.length ? tokens.reduce((a, b) => a + b, 0) / tokens.length : undefined)}。`, '',
    '括号为有记录案例数/报告内案例数；缺失值不按零计算。累计案例耗时不是批次墙钟时间。',
    '检视用量来自最终答卷，可能不含失败和重跑成本；裁判与翻译分别统计，翻译用量见原始记录。', ''].join('\n');
}

export function reportScoreScope(text: string): Set<string> | undefined {
  const sections = reportSections(text);
  const score = sections.find((section) => section.title === '评分结果');
  const count = score?.text.match(/案例 ([\d,]+) 个；参考意见/);
  if (!count) return undefined;
  const references = sections.find((section) => section.title === '参考答案与匹配情况');
  if (!references) return undefined;
  const ids = new Set(caseSections(references).flatMap((section) => {
    const match = section.title.match(/^参考答案与匹配情况 · ([^ ]+)(?: · 第.*)?$/);
    return match ? [match[1]] : [];
  }));
  return ids.size === Number(count[1].replaceAll(',', '')) ? ids : undefined;
}

function caseTable(run: Run, usage: string, scoredIds?: Set<string>) {
  const rows = usage.split('\n').filter((line) => /^\| 检视：/.test(line));
  const values = new Map(rows.map((line) => {
    const parts = line.split('|').slice(1, -1).map((part) => part.trim());
    return [parts[0].slice(3), parts];
  }));
  return ['## 案例结果简表', '', '状态为当前批次状态；“计入本报告评分”依据所选报告的评分明细。耗时和 Token 来自所选报告。', '',
    '| 案例 | 当前状态 | 计入本报告评分 | 耗时（秒） | Token |', '|---|---|---|---:|---:|',
    ...run.cases.map((item) => {
      const data = values.get(item.id);
      const scored = scoredIds === undefined ? '未提供' : scoredIds.has(item.id) ? '是' : '否';
      return `| ${cell(item.id)} | ${cell(labels[item.status] ?? item.status)} | ${scored} | ${data?.[1] ?? '未提供'} | ${data?.[4] ?? '未提供'} |`;
    }), ''].join('\n');
}

function snapshotMetrics(run: Run) {
  const percent = (value: number | undefined) => value === undefined ? '未提供' : `${(value * 100).toFixed(1)}%`;
  return ['## 当前批次评分汇总', '', '| Precision | Recall | F1 |', '|---:|---:|---:|',
    `| ${percent(run.metrics?.precision)} | ${percent(run.metrics?.recall)} | ${percent(run.metrics?.f1)} |`,
    '', '来自当前批次评分记录；完整报告中的评分口径优先。', ''].join('\n');
}

export function reportSummary(text: string, run: Run) {
  const sections = reportSections(text);
  const scoredIds = reportScoreScope(text);
  const intro = sections[0].text.replace(/^(>.*?)(?:排除案例：|未完成案例：).*$/gm, '$1排除清单见完整报告；未完成案例不计零分。');
  const usage = sections.find((section) => section.title === '耗时与用量')?.text ?? '';
  const scores = sections.filter((section) => ['评分结果', '评分汇总', '测评结果'].includes(section.title));
  return [intro, '## 测评对象', '',
    `模型：${cell(run.model)}；Agent：${cell(run.reviewer)}；案例集：${cell(run.suite)}；评分器：${cell(run.evaluator)}；裁判：${cell(run.judge_model ?? '未提供')}。`, '',
    ...(run.skill_mode === 'baseline' ? [`无待测 Skill 基线；DSH ${cell(run.dsh_runtime_version ?? '未提供')}。`, ''] : []),
    ...(run.skill_mode === 'skill' ? [`Skill：${cell(run.skill_name ?? '未提供')}；版本：${cell(run.skill_version ?? '未提供')}；DSH ${cell(run.dsh_runtime_version ?? '未提供')}。`, ''] : []),
    ...(run.dsh_runtime_mode === 'pod' ? ['运行条件：POD 内运行，无操作系统级沙盒；仅软件工具和路径限制。', ''] : []),
    '## 完成情况', '',
    `计划 ${run.total} 例；当前完整答卷 ${run.completed} 例；本报告评分范围 ${number(scoredIds?.size)} 例。`, '',
    '评分结果仅代表纳入评分的案例，未完成案例不计零分。当前答卷数可能包含评分后补跑的案例。', '',
    ...scores.map((section) => section.text),
    scores.length || intro.includes('| F1') ? '' : snapshotMetrics(run),
    'Precision 表示检视意见与参考答案的匹配程度，Recall 表示参考问题的覆盖程度，F1 综合两者。具体计数规则以本报告评分协议为准。', '',
    usageSummary(usage),
    caseTable(run, usage, scoredIds), '具体检视意见、参考答案、匹配过程和恢复记录请查看“案例明细”或下载完整报告。', ''].join('\n');
}

export function reportDetails(text: string) {
  return reportSections(text).filter((section) => !['', '评分结果', '评分汇总', '测评结果'].includes(section.title))
    .flatMap(caseSections);
}
function caseSections(section: ReportSection): ReportSection[] {
  if (!['检视工具发现的问题', '参考答案与匹配情况'].includes(section.title)) return [section];
  const chunks: ReportSection[] = [];
  let fence = '';
  for (const line of section.text.split('\n')) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) fence = fence ? '' : marker;
    const match = !fence && /^(?:案例：|### 案例 )`([^`]+)`(.*)/.exec(line);
    if (match) chunks.push({ title: `${section.title} · ${match[1]}${match[2].startsWith(' · 第') ? match[2] : ''}`, text: '' });
    if (chunks.length) chunks[chunks.length - 1].text += line + '\n';
  }
  return chunks.length ? chunks : [section];
}
