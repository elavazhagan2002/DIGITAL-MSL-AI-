/**
 * Medical literacy research API — search and import from transient connectors.
 */

import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import { getAccessToken } from '../utils/tokenUtils';

export interface LiteraturePreview {
  source: string;
  record_id: string;
  title: string;
  subtitle: string;
  summary: string;
  url: string;
  published_date: string;
  pmcid?: string | null;
  has_pmc_pdf?: boolean;
  already_loaded?: boolean;
  pdf_urls?: string[];
  pmc_url?: string | null;
  selection_key: string;
  /** 0-100 title relevance to the search query */
  accuracy_pct?: number;
}

export interface LiteratureSearchResponse {
  results: LiteraturePreview[];
  count: number;
  errors: string[];
  connectors_used: string[];
}

export interface LiteratureImportResponse {
  uploaded_count: number;
  uploaded_documents: Array<Record<string, unknown>>;
  pubmed_count: number;
  clinicaltrials_count: number;
  skipped_count: number;
  errors: string[];
}

function authHeaders(): HeadersInit {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail.map((item: { msg?: string }) => item.msg || JSON.stringify(item)).join('; ');
    }
    if (body?.message) return String(body.message);
  } catch {
    // fall through
  }
  return `Request failed (${response.status})`;
}

function networkErrorMessage(error: unknown): Error {
  if (error instanceof Error && (error.message === 'Failed to fetch' || error.name === 'TypeError')) {
    return new Error('Cannot reach the backend. Confirm the API is running and try again.');
  }
  return error instanceof Error ? error : new Error('Network request failed');
}

async function fetchMedicalLiteracy(
  path: string,
  payload: unknown,
  options?: { signal?: AbortSignal }
): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: {
        ...authHeaders(),
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
  } catch (error) {
    throw networkErrorMessage(error);
  }
}

export function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = String((error as { name?: string }).name || '');
  return name === 'AbortError';
}

export async function searchMedicalLiterature(
  payload: {
    repository_id: string;
    query: string;
    connectors: string[];
    free_full_text_only?: boolean;
    format_terms_as_mesh?: boolean;
    pubmed_max_results?: number;
    clinicaltrials_max_results?: number;
  },
  options?: { signal?: AbortSignal }
): Promise<LiteratureSearchResponse> {
  const response = await fetchMedicalLiteracy('/medical-literacy/search', payload, options);
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json();
}

export async function importMedicalLiterature(
  payload: {
    repository_id: string;
    selections: LiteraturePreview[];
  },
  options?: { signal?: AbortSignal }
): Promise<LiteratureImportResponse> {
  const response = await fetchMedicalLiteracy('/medical-literacy/import', payload, options);
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json();
}
