import { fetchEventSource } from '@microsoft/fetch-event-source';
import { request } from '../utils/request';

export interface ResearchSubAgent {
  id: string; revision: number; question_index: number; question: string;
  status: 'running' | 'paused' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
  phase: string; source_count?: number;
}

export interface AgentRun {
  id: string;
  conversation_id: string;
  status: 'queued' | 'running' | 'paused' | 'interrupted' | 'failed' | 'awaiting_input' | 'succeeded' | 'cancelled';
  answer: string;
  error: string;
  pause_requested: boolean;
  cancel_requested: boolean;
  user_input: string;
  assistant_history_id: string;
  agent: { id: string; name: string; logo_url: string };
  unknown_tools: { id: string; name: string; error: string }[];
  artifacts?: { job_id: string; name: string; sha256: string; bytes: number; download_url: string }[];
  research?: { phase: string; direction: string; revision?: number; source_count?: number; read_count?: number; verified_count?: number;
    delegation?: { mode?: 'serial' | 'parallel'; reason?: string; question_indices?: number[]; max_concurrency?: number }; agents?: ResearchSubAgent[];
    plan?: { scope?: string; questions?: { question: string; query: string }[] } };
}
export type ToolDecisions = Record<string, 'retry' | 'skip'>;
export const createRunAPI = (data: Record<string, unknown>) => request({ url: '/api/v1/runs', method: 'POST', data });
export const activeRunAPI = (conversationId: string) => request({ url: '/api/v1/runs/active', params: { conversation_id: conversationId } });
export const latestRunAPI = (conversationId: string) => request({ url: '/api/v1/runs/latest', params: { conversation_id: conversationId } });
export const getRunAPI = (id: string) => request({ url: `/api/v1/runs/${id}` });
export const resumeRunAPI = (id: string, decisions: ToolDecisions, direction?: string) => request({ url: `/api/v1/runs/${id}/resume`, method: 'POST', data: { decisions, ...(direction?.trim() ? { research_direction: direction.trim() } : {}) } });
export const controlRunAPI = (id: string, action: 'pause' | 'cancel') => request({ url: `/api/v1/runs/${id}/${action}`, method: 'POST' });

export async function watchRun(id: string, onEvent: (event: any) => void, signal: AbortSignal) {
  await fetchEventSource(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/runs/${id}/events`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
    signal, openWhenHidden: true,
    async onopen(response) {
      if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('任务进度连接失败，请重新连接');
    },
    onmessage(event) { if (event.data) onEvent(JSON.parse(event.data)); },
    onerror(error) { throw error; },
  });
}

export async function exportRun(id: string) {
  const response = await request({ url: `/api/v1/runs/${id}/export`, responseType: 'blob' });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url; link.download = `task-${id}.jsonl`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function exportResearch(id: string, format: 'markdown' | 'json') {
  const response = await request({ url: `/api/v1/runs/${id}/research/export`, params: { format }, responseType: 'blob' });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url; link.download = `research-${id}.${format === 'markdown' ? 'md' : 'json'}`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadSandboxArtifact(runId: string, jobId: string, name: string) {
  const response = await request({ url: `/api/v1/runs/${encodeURIComponent(runId)}/sandbox/${encodeURIComponent(jobId)}/download`, responseType: 'blob' });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
