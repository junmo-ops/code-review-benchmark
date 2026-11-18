import { useEffect, useRef, useState } from 'react';
import { readPage } from './inventory';
import type { CosObject } from './inventory';
export function useInventory() {
  const [items, setItems] = useState<CosObject[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [complete, setComplete] = useState(false), [prefix, setPrefix] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function scan(scope: string) {
    if (controller.current) return;
    const active = new AbortController(); controller.current = active;
    setBusy(true); setError(''); setComplete(false); setItems([]); setPrefix('');
    try {
      const found = new Map<string, CosObject>();
      const cursors = new Set<string>();
      let cursor = '';
      do {
        const page = await readPage(scope, cursor, active.signal);
        if (active.signal.aborted) return;
        page.items.forEach((row) => found.set(row.key, row));
        setItems([...found.values()]); setPrefix(page.prefix);
        cursor = page.cursor;
        if (cursor && cursors.has(cursor)) throw new Error('分页游标重复，扫描已停止；当前结果不完整');
        cursors.add(cursor);
        if (cursor && found.size >= 100000) throw new Error('已扫描 10 万个对象，达到本页上限；当前结果不完整，请导出后使用 COS 清单继续盘点');
      } while (cursor);
      setComplete(true);
    } catch (cause) {
      if (!active.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      controller.current = null; setBusy(false);
    }
  }
  return { items, busy, error, complete, prefix, scan, stop: () => controller.current?.abort() };
}
