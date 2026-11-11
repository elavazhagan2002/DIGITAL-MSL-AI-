/**
 * Load Documents: calls for the page that lets a Physical MSL load documents into a repository.
 * New module (the older repository service is left as it was). Raw server text never reaches the UI:
 * every failure is an ApiError carrying only status, code and request id.
 */
import { API_BASE_URL } from '../config/api';
import { ApiError } from './digitalMslService';
import { dmslFetch } from './digitalMslService';
import { getMemoryToken, readCookie } from './apiTransport';

export type LoadDocsState = 'ok' | 'not_configured' | 'ikm_unreachable' | 'unauthorised' | 'no_repositories';

export interface LoadDocsRepository {
  repository_id: string;
  name: string;
  therapeutic_area: string;
  approved: boolean;
  document_count?: number | null;
  demo: boolean;
}

export interface LoadDocsContext {
  state: LoadDocsState;
  code: string;
  demo: boolean;
  request_id: string;
  allow_list: { empty: boolean; all: boolean; approved_count: number };
  ikm: { configured: boolean; reachable: boolean; unauthorised: boolean; code: string; checked_at: string };
  missing: string[];
  repositories: LoadDocsRepository[];
}

export interface LoadedDocument {
  document_id: string;
  original_file_name: string;
  status: string;
  document_type?: string;
  upload_timestamp?: number;
  error_details?: string | null;
  demo?: boolean;
}

export type IngestionStatus = 'queued' | 'processing' | 'ready' | 'failed';

export const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.doc', '.txt'] as const;
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

async function toApiError(res: Response): Promise<ApiError> {
  let code: string | undefined;
  let requestId = res.headers.get('x-request-id') || undefined;
  let detail = '';
  try {
    const body = await res.json();
    code = typeof body?.code === 'string' ? body.code : undefined;
    requestId = (typeof body?.request_id === 'string' ? body.request_id : undefined) || requestId;
    detail = typeof body?.detail === 'string' ? body.detail : '';
  } catch {
    /* body is optional */
  }
  // Only short, human-written 4xx details (for example "Unsupported file type") are kept as the message.
  const message = res.status >= 400 && res.status < 500 && res.status !== 401 && res.status !== 403 ? detail : 'Request failed';
  return new ApiError(message || 'Request failed', res.status, code, undefined, requestId);
}

export async function fetchLoadDocsContext(): Promise<LoadDocsContext> {
  const res = await dmslFetch(`${API_BASE_URL}/api/v1/digital-msl/load-documents/context`, undefined, 30000);
  if (!res.ok) throw await toApiError(res);
  return res.json();
}

export async function fetchLoadedDocuments(repositoryId: string): Promise<{ documents: LoadedDocument[]; demo: boolean }> {
  const res = await dmslFetch(`${API_BASE_URL}/api/v1/repositories/${encodeURIComponent(repositoryId)}/documents`, undefined, 30000);
  if (!res.ok) throw await toApiError(res);
  const body = await res.json();
  return { documents: body.documents || [], demo: Boolean(body.demo) };
}

/** Map whatever the document store says to the four statuses the page shows. */
export function normalizeStatus(raw: string | undefined | null): IngestionStatus {
  const s = String(raw || '').toLowerCase();
  if (/fail|error|reject/.test(s)) return 'failed';
  if (/complete|ready|indexed|processed|success|done/.test(s)) return 'ready';
  if (/process|chunk|vector|embed|running|extract/.test(s)) return 'processing';
  return 'queued';
}

export function validateFile(file: File): string {
  const name = file.name || '';
  const ext = name.includes('.') ? `.${name.split('.').pop()!.toLowerCase()}` : '';
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(ext)) return `Only ${ALLOWED_EXTENSIONS.join(', ')} files can be loaded.`;
  if (file.size === 0) return 'This file is empty.';
  if (file.size > MAX_UPLOAD_BYTES) return `This file is larger than ${Math.round(MAX_UPLOAD_BYTES / 1024 / 1024)} MB.`;
  return '';
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Upload one file with progress. fetch cannot report upload progress, so this uses XMLHttpRequest and
 * adds the same credentials and CSRF header the shared fetch wrapper adds.
 */
export function uploadFileWithProgress(
  repositoryId: string,
  file: File,
  onProgress: (percent: number) => void,
): Promise<{ document_id?: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE_URL}/api/v1/digital-msl/therapeutic-areas/${encodeURIComponent(repositoryId)}/documents/upload`);
    xhr.withCredentials = true;
    const csrf = readCookie('dmsl_csrf');
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
    const token = getMemoryToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
    };
    xhr.onerror = () => reject(new ApiError('Network error', 0));
    xhr.ontimeout = () => reject(new ApiError('Timed out', 0));
    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(xhr.responseText || '{}');
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        const docs = (body.documents as Array<{ document_id?: string }> | undefined) || [];
        resolve({ document_id: docs[0]?.document_id });
        return;
      }
      const detail = typeof body.detail === 'string' ? body.detail : '';
      const requestId = (typeof body.request_id === 'string' ? body.request_id : undefined) || xhr.getResponseHeader('x-request-id') || undefined;
      const keep = xhr.status >= 400 && xhr.status < 500 && xhr.status !== 401 && xhr.status !== 403;
      reject(new ApiError(keep ? detail || 'Upload failed' : 'Upload failed', xhr.status, typeof body.code === 'string' ? body.code : undefined, undefined, requestId));
    };
    const form = new FormData();
    form.append('files', file);
    xhr.send(form);
  });
}
