/**
 * Session transport for the API.
 *
 * The backend keeps the session in an HttpOnly cookie (`dmsl_session`) and issues a readable
 * CSRF cookie (`dmsl_csrf`). Every request to the API therefore carries credentials, and every
 * state-changing request echoes the CSRF cookie in `X-CSRF-Token`. This is installed once on
 * `window.fetch`, so streaming (SSE) calls and the other service modules are covered too.
 *
 * A bearer token is only ever held in memory (never persisted) and only used when present.
 */
import { API_BASE_URL } from '../config/api';

const CSRF_COOKIE = 'dmsl_csrf';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

let memoryToken = '';

export function setMemoryToken(token: string): void {
  memoryToken = token || '';
}

export function getMemoryToken(): string {
  return memoryToken;
}

export function readCookie(name: string, cookieString?: string): string {
  const source = cookieString ?? (typeof document !== 'undefined' ? document.cookie : '');
  for (const part of source.split(';')) {
    const index = part.indexOf('=');
    if (index < 0) continue;
    if (part.slice(0, index).trim() === name) {
      try {
        return decodeURIComponent(part.slice(index + 1).trim());
      } catch {
        return part.slice(index + 1).trim();
      }
    }
  }
  return '';
}

export function csrfToken(): string {
  return readCookie(CSRF_COOKIE);
}

function isApiUrl(input: RequestInfo | URL): boolean {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  return url.startsWith(API_BASE_URL) || url.startsWith('/api/');
}

let installed = false;

export function installApiTransport(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    if (!isApiUrl(input)) return original(input, init);
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (!SAFE_METHODS.has(method) && !headers.has('X-CSRF-Token')) {
      const token = csrfToken();
      if (token) headers.set('X-CSRF-Token', token);
    }
    if (memoryToken && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${memoryToken}`);
    return original(input, { ...init, headers, credentials: 'include' });
  };
}
