import { createContext, useContext } from 'react';
import type { Snapshot, ImportTask } from './types';
export interface Workbench {
  state: Snapshot;
  task: ImportTask | null;
  importBusy: boolean;
  refresh: () => Promise<void>;
  setTask: (task: ImportTask | null) => void;
  newRun: (suite?: string, dataset?: string) => void;
  openRun: (key: string, job?: string) => void;
  openExperiment: (key: string, results?: boolean) => void;
}
export const WorkbenchContext = createContext<Workbench | undefined>(undefined);
export function useWorkbench() {
  const context = useContext(WorkbenchContext);
  if (!context) throw new Error('工作台上下文不可用');
  return context;
}
