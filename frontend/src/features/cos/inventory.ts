import { api } from '../../api';
export interface CosObject { key: string; size: number; modified: string; category: string; advice: string }
export interface InventoryPage { items: CosObject[]; cursor: string; prefix: string; bucket: string }
export const scopes = [{ value: 'runs', label: '运行数据与集群证据' }, { value: 'resources', label: '案例、仓库和 Skill' }, { value: 'models', label: '模型配置' }];
export function readPage(scope: string, cursor: string, signal: AbortSignal) {
  return api<InventoryPage>('/api/cos/inventory?' + new URLSearchParams({ scope, cursor }), undefined, undefined, signal);
}
export function bytes(size: number) {
  if (size < 1024) return `${size} B`;
  const power = Math.min(Math.floor(Math.log(size) / Math.log(1024)), 4);
  return `${(size / 1024 ** power).toFixed(2)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][power]}`;
}
export function exportInventory(items: CosObject[], complete: boolean, prefix: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ scanned_at: new Date().toISOString(), complete, prefix, reference_check: 'not_performed', items }, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = 'cos-inventory.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
