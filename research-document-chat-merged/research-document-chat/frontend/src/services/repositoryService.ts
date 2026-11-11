import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import { portalAuthHeaders } from './digitalMslService';

export type TransientConnectorId = 'pubmed' | 'clinicaltrials' | 'sharepoint' | 's3';

export interface RepositorySettings {
  transient_connectors?: { enabled?: boolean; connectors?: string[] };
  medical_literacy_research?: boolean;
}

export const TRANSIENT_CONNECTOR_OPTIONS: Array<{ id: TransientConnectorId; label: string }> = [
  { id: 'pubmed', label: 'PubMed' },
  { id: 'clinicaltrials', label: 'ClinicalTrials.gov' },
  { id: 'sharepoint', label: 'SharePoint' },
  { id: 's3', label: 'Amazon S3' },
];

export interface Repository {
  repository_id: string;
  name: string;
  description?: string;
  status?: string;
  document_count?: number;
  settings?: RepositorySettings;
}

export function repositoryConfiguredConnectors(repo: Repository | null | undefined): string[] {
  const settings = repo?.settings || {};
  const transient = settings.transient_connectors || {};
  const selected = Array.isArray(transient.connectors) ? transient.connectors : [];
  const cleaned = selected.map((item) => String(item || '').trim().toLowerCase()).filter(Boolean);
  if (cleaned.length > 0) return cleaned;
  if (settings.medical_literacy_research || transient.enabled) return ['pubmed', 'clinicaltrials'];
  // Standalone application defaults to the two supported literature connectors.
  return ['pubmed', 'clinicaltrials'];
}

export interface RepositoryDocument {
  document_id: string;
  original_file_name: string;
  status: string;
  document_type?: string;
  upload_timestamp?: number;
  created_at?: string;
}

export interface DocumentChunk {
  chunk_id: string;
  page: number | string | null;
  section: string;
  line_start?: number | null;
  line_end?: number | null;
  strategy?: string | null;
  text: string;
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

export async function createRepository(name: string, description: string): Promise<Repository> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.REPOSITORIES}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function uploadRepositoryDocuments(repoId: string, files: File[]): Promise<unknown> {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.REPOSITORY_UPLOAD(repoId)}`, {
    method: 'POST',
    headers: portalAuthHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function getDocumentChunks(docId: string): Promise<{ document_name: string; chunks: DocumentChunk[] }> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_CHUNKS(docId)}`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function listRepositories(): Promise<Repository[]> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.REPOSITORIES}`, { signal: controller.signal });
    if (!res.ok) {
      let code: string | undefined;
      let requestId: string | undefined = res.headers.get('x-request-id') || undefined;
      try {
        const body = await res.json();
        code = typeof body?.code === 'string' ? body.code : undefined;
        requestId = (typeof body?.request_id === 'string' ? body.request_id : undefined) || requestId;
      } catch {
        /* body is optional */
      }
      const { ApiError } = await import('./digitalMslService');
      throw new ApiError('Repositories unavailable', res.status, code, undefined, requestId);
    }
    const data = await res.json();
    return data.repositories || [];
  } catch (err) {
    const message = err instanceof Error ? err.message : '';
    if (/abort/i.test(message) || (err instanceof DOMException && err.name === 'AbortError')) {
      throw new Error('Repository list timed out. Try refresh.');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function listLibrariesForAssignment(): Promise<Array<{ repository_id: string; name: string }>> {
  try {
    const { fetchQuery } = await import('../lib/queryCache');
    const repos = (await fetchQuery('repositories', listRepositories, { staleMs: 60_000 })).filter((r) => !r.name?.startsWith('_temp_'));
    if (repos.length) {
      return repos.map((r) => ({ repository_id: r.repository_id, name: r.name || r.repository_id }));
    }
  } catch {
    /* fall through to Digital MSL list */
  }
  try {
    const { listDmslLibraries } = await import('./digitalMslService');
    return await listDmslLibraries();
  } catch {
    return [];
  }
}

export async function listRepositoryDocuments(repoId: string): Promise<RepositoryDocument[]> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.REPOSITORY_DOCUMENTS(repoId)}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch documents: ${res.statusText}`);
  }
  const data = await res.json();
  return data.documents || [];
}

export async function getDocumentPreview(docId: string): Promise<any> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_PREVIEW(docId)}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch preview: ${res.statusText}`);
  }
  return await res.json();
}
