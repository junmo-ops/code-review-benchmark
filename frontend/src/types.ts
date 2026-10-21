export interface Dataset {
  id: string;
  name?: string;
  count: number;
  rounds?: number;
}
export interface Suite {
  id: string;
  name: string;
  role: string;
  description: string;
  datasets: Dataset[];
}
export interface Profile {
  id: string;
  label: string;
  provider?: string;
  review_model: string;
  judge_model?: string;
  base_url?: string;
  stream_enabled?: boolean;
  internal?: boolean;
  review_ready?: boolean;
  ready?: boolean;
}
export interface Metrics {
  precision?: number;
  recall?: number;
  f1?: number;
  test_pass_rate?: number;
  state_accuracy?: number;
  transition_accuracy?: number;
  state_coverage?: number;
  pairs?: [number, number][];
}
export interface Case {
  review_coverage?: string;
  review_details?: { kind: string; file?: string | null; message: string }[];
  id: string;
  repo?: string;
  status: string;
  scored?: boolean | null;
  elapsed_seconds?: number;
  metrics?: Metrics;
  error?: string;
}
export interface Artifact {
  path: string;
  size: number;
  report?: boolean;
}
export interface Run {
  key: string;
  name: string;
  display_name?: string;
  suite: string;
  model: string;
  judge_model?: string;
  reviewer: string;
  evaluator: string;
  status: string;
  total: number;
  completed: number;
  scored_cases?: number;
  score_locked_reason?: string;
  created?: string;
  profile?: string;
  metrics?: Metrics;
  cases: Case[];
  artifacts: Artifact[];
  compatibility_warning?: string;
  simulation?: boolean;
  can_run?: boolean;
  case_jobs?: number;
  max_case_retries?: number;
  repair_executed?: boolean;
  case_resources_purged?: boolean;
}
export interface Job {
  id: string;
  key: string;
  action: string;
  status: string;
  message?: string;
  step?: string;
  created_at?: string;
  last_output_at?: string;
  elapsed_seconds?: number;
  completed_steps?: number;
  total_steps?: number;
  start_after_create?: boolean;
}
export interface Repository {
  repo: string;
  slug: string;
  versions: { sha256: string; size: number; current?: boolean }[];
}
export interface Assets {
  ready: boolean;
  message?: string;
  upload_max_bytes?: number;
  upload_max_label?: string;
  repositories?: Repository[];
  datasets?: Dataset[];
}
export interface Group {
  key: string;
  label: string;
  model: string;
  cases: Case[];
  completed: number;
  scored: number;
  failed: number;
  timeouts: number;
  common_quality?: Metrics;
  mean_seconds?: number;
  review_tokens: { total_tokens?: number };
  token_coverage: number;
}
export interface Comparison {
  key: string;
  name: string;
  display_name?: string;
  suite: string;
  judge_model: string;
  status: string;
  job_id?: string;
  budget?: { judge_seconds?: number; review_seconds?: number };
  report: { groups: Group[]; common_cases: string[]; case_count: number };
}
export interface Snapshot {
  platform: string;
  queue_mode?: boolean;
  active?: string;
  worker_ready?: boolean;
  recovery_required?: boolean;
  access_role?: string;
  storage_mode?: string;
  run_storage?: { status?: string; message?: string; last_sync_at?: string };
  catalog: Suite[];
  runs: Run[];
  jobs: Job[];
  profiles: Profile[];
  comparisons: Comparison[];
  case_assets?: Assets;
}
export interface ImportTask {
  id: string;
  kind?: string;
  status: string;
  phase?: string;
  message: string;
  completed: number;
  total: number;
}
export interface TextReply {
  text: string;
  truncated?: boolean;
}
export interface JobReply {
  key: string;
  job_id: string;
}
export interface UploadReply {
  message: string;
  task?: ImportTask;
}
export interface UploadSession {
  task?: ImportTask;
  id: string;
  kind: 'dataset' | 'repository';
  filename: string;
  size: number;
  fingerprint: string;
  offset: number;
  chunk_size: number;
  status: string;
}
export interface ModelValues {
  provider: string;
  name: string;
  model: string;
  base_url: string;
  api_key?: string;
  stream: boolean;
}
export interface Connection {
  results: {
    role: string;
    status: string;
    message?: string;
    elapsed_seconds: number;
    http_status?: number;
  }[];
}
export interface Evidence {
  status: Case;
  comments: { content: string; path: string; start_line: number }[];
  references: { comment: string }[];
  artifacts: string[];
  score: {
    status: string;
    metrics?: Metrics;
    decisions: { golden: number; candidate: number; match: boolean; reasoning?: string }[];
  };
  diagnostic?: {
    ended_calls: number;
    retries: number;
    http_statuses: Record<string, number>;
    deadline_calls: { task: string; file: string; seconds: number }[];
    pending_calls: { task: string; file: string }[];
  };
}
