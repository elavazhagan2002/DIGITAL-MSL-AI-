import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import { getMemoryToken } from './apiTransport';

export interface ClaimEvidence {
  chunk_id: string;
  document_id: string;
  document_name: string;
  document_content: string;
  page_no: number;
  paragraph: string;
  section?: string;
  original_text: string;
  line_start?: number;
  line_end?: number;
  claim_text?: string;
}

export interface ClaimValidation {
  score: number;
  verdict: 'supported' | 'partially_supported' | 'unsupported';
  signals: Record<string, unknown>;
  reasons: string[];
}

export interface Claim {
  claim_id: string;
  claim_text: string;
  evidence: ClaimEvidence[];
  validation?: ClaimValidation | null;
}

export interface AgentResponse {
  message_id: string;
  correlation_id: string;
  conversation_id: string;
  channel: string;
  status: string;
  decision: 'respond' | 'escalate_to_msl' | 'refuse';
  answer: string;
  overall_score: number;
  threshold: number;
  claims: Claim[];
  msl_case_id?: string | null;
  reason_codes: string[];
  trace_id: string;
  elapsed_ms?: number;
  timing?: Record<string, number>;
  session_id?: string;
  documents_found?: string[];
  references?: MslCaseReference[];
  presentation_body?: string;
  presentation_references?: string[];
  intent?: string;
  message_type?: 'assistant_note' | 'answer' | 'escalation' | 'refusal' | 'safety';
  retrieval_ran?: boolean;
  llm_called?: boolean;
  actions?: Array<'ask_msl' | 'helpful' | 'send_to_msl'>;
  metrics?: { retrieval_confidence: number; groundedness: number; response_time_ms: number } | null;
}

export interface MslCaseReference {
  id?: string;
  title: string;
  source: string;
  page_no?: number;
  paragraph?: string;
  quote?: string;
  is_external?: boolean;
  verification_status?: string;
}

export interface MslCaseClaim {
  text: string;
  type: 'supported' | 'unsupported' | 'commentary';
  reference_ids?: string[];
}

export interface MslCase {
  case_id: string;
  correlation_id: string;
  source_message_id?: string;
  trace_id: string;
  conversation_id?: string;
  channel: string;
  external_sender_id: string;
  doctor_id: string;
  assigned_msl_id: string;
  repository_id?: string;
  question: string;
  reason_codes: string[];
  status: string;
  priority: string;
  resolution?: string | null;
  draft_response?: string;
  draft_status?: string;
  approval_status?: string;
  reviewer_notes?: string;
  references?: MslCaseReference[];
  claims?: MslCaseClaim[];
  version?: number;
  author_id?: string;
  reviewer_id?: string;
  queue_notice?: string;
  created_at: string;
  updated_at?: string | null;
  trace?: Record<string, unknown>;
  conversation?: ConversationDetail;
}

export interface ConversationSummary {
  conversation_id: string;
  doctor_id: string;
  doctor_name: string;
  channel: string;
  message_count: number;
  start_time: string | null;
  last_activity: string | null;
  latest_question: string;
  status: string;
  has_escalation: boolean;
  msl_case_id?: string | null;
  msl_case_status?: string | null;
  validation_outcome: string;
  assigned_msl_id?: string | null;
  session_id?: string;
}

export interface ConversationMessage {
  message_id: string;
  correlation_id?: string;
  question: string | null;
  received_at: string | null;
  status: string;
  channel: string;
  is_msl_response: boolean;
  author_name?: string;
  response?: AgentResponse | null;
  trace?: TraceDetail | null;
}

export interface ConversationDetail {
  conversation_id: string;
  doctor_id: string;
  doctor_name: string;
  channel: string;
  start_time: string | null;
  last_activity: string | null;
  messages: ConversationMessage[];
  case?: MslCase | null;
}

export interface TraceSummary {
  trace_id: string;
  correlation_id?: string;
  channel?: string;
  user_query: string;
  overall_score: number;
  decision: string;
  created_at?: string | null;
}

export interface TraceDetail extends TraceSummary {
  pre_validation: unknown;
  post_validation: unknown;
  retrieval?: unknown;
}

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

export function portalAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getMemoryToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

function jsonAuthHeaders(): Record<string, string> {
  return portalAuthHeaders({ 'Content-Type': 'application/json' });
}

export const SESSION_EXPIRED_EVENT = 'dmsl:session-expired';

/** The server expires sessions: drop the stored session so the app returns to the login page. */
async function handleSessionExpiry(res: Response): Promise<void> {
  if (typeof localStorage === 'undefined' || !localStorage.getItem('dmsl_portal_session')) return;
  let expired = res.status === 401;
  if (!expired && res.status === 403) {
    try {
      const body = await res.clone().json();
      expired = typeof body?.detail === 'string' && /invalid or expired session|session has ended/i.test(body.detail);
    } catch {
      /* not JSON */
    }
  }
  if (!expired) return;
  localStorage.removeItem('dmsl_portal_session');
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

export async function dmslFetch(url: string, init?: RequestInit, timeoutMs = 45000): Promise<Response> {
  const headers = new Headers(init?.headers ?? {});
  Object.entries(portalAuthHeaders()).forEach(([key, value]) => headers.set(key, value));
  const timeoutController = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    timeoutController.abort();
  }, timeoutMs);
  const parentSignal = init?.signal;
  if (parentSignal) {
    if (parentSignal.aborted) {
      clearTimeout(timer);
      throw new Error('Request cancelled.');
    }
    parentSignal.addEventListener('abort', () => timeoutController.abort(), { once: true });
  }
  try {
    const res = await fetch(url, { ...init, headers, signal: timeoutController.signal });
    await handleSessionExpiry(res);
    return res;
  } catch (err) {
    const message = err instanceof Error ? err.message : '';
    if (timedOut || /abort/i.test(message) || (err instanceof DOMException && err.name === 'AbortError')) {
      throw new Error('Request timed out. Check that the backend and document service are running.');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export interface PortalUser {
  user_id: string;
  email: string;
  display_name: string;
  role: 'admin' | 'doctor' | 'msl';
  assigned_msl_id: string;
  assigned_group_id?: string;
  assigned_repository_ids?: string[];
  default_repository_id?: string;
  status: string;
  verification_status?: VerificationStatus;
  consents?: { whatsapp_messaging: boolean; email_messaging: boolean };
  mfa_enabled?: boolean;
  mfa_required?: boolean;
}

export type VerificationStatus =
  | 'PENDING_CONTACT_VERIFICATION'
  | 'PENDING_REGISTRATION_VERIFICATION'
  | 'VERIFIED'
  | 'REJECTED'
  | 'NEEDS_INFO';

export const VERIFICATION_STATUSES: VerificationStatus[] = [
  'PENDING_CONTACT_VERIFICATION',
  'PENDING_REGISTRATION_VERIFICATION',
  'VERIFIED',
  'REJECTED',
  'NEEDS_INFO',
];

export interface CertificateInfo {
  present?: boolean;
  scan_status?: string;
  uploaded_at?: string | null;
}

export interface DoctorProfile {
  user_id: string;
  mobile: string;
  registration_number: string;
  registration_council: string;
  registration_year: number | null;
  qualification: string;
  highest_qualification?: string;
  speciality: string;
  institution: string;
  practice_type?: string;
  city: string;
  state: string;
  pincode?: string;
  nmr_id?: string;
  verification_status: VerificationStatus;
  verification_notes?: string;
  rejection_reason?: string | null;
  verified_at?: string | null;
  verified_by_name?: string | null;
  certificate?: CertificateInfo | null;
}

export interface DoctorRegistryEntry extends DoctorProfile {
  display_name: string;
  email: string;
  status: string;
  assigned_repository_ids: string[];
  default_repository_id: string;
  consents: { whatsapp_messaging: boolean; email_messaging: boolean };
  registered_at: string | null;
  last_contact_at?: string | null;
  conversation_count?: number;
  escalation_count?: number;
}

export interface ConsentState {
  granted: boolean;
  version: string;
  updated_at: string | null;
}

export interface ConsentsState {
  whatsapp_messaging: ConsentState;
  email_messaging: ConsentState;
  data_processing?: ConsentState;
  terms_privacy?: ConsentState;
  hcp_declaration?: ConsentState;
}

export type ConsentType = 'terms_privacy' | 'hcp_declaration' | 'whatsapp_messaging' | 'email_messaging';

export interface ConsentDocumentItem {
  type: ConsentType;
  required: boolean;
  title: string;
  text: string;
}

export interface CouncilOption {
  id: string;
  name: string;
}

export interface RegisterOptions {
  therapeutic_areas: Array<{
    repository_id: string;
    name: string;
    description?: string;
    scope_keywords?: string[];
    enabled?: boolean;
    coming_soon?: boolean;
  }>;
  councils: Array<CouncilOption | string>;
  specialities: string[];
  qualifications: string[];
  practice_types?: string[];
  consent_documents: { version: string; items: ConsentDocumentItem[] };
}

export interface DoctorRegisterPayload {
  email: string;
  display_name: string;
  password: string;
  mobile: string;
  registration_number: string;
  registration_council: string;
  council_id?: string;
  registration_year: number;
  qualification: string;
  highest_qualification: string;
  speciality: string;
  institution: string;
  city: string;
  state: string;
  pincode: string;
  nmr_id?: string;
  repository_ids: string[];
  default_repository_id: string;
  consents: Record<ConsentType, boolean>;
  consent_version: string;
}

export interface RegisterResult {
  status: 'verification_required';
  user_id: string;
  email_masked?: string;
  mobile_masked?: string;
  otp_expires_in?: number;
  dev_codes?: { email?: string; mobile?: string };
}

export interface VerifyContactResult {
  email_verified: boolean;
  mobile_verified: boolean;
  status: VerificationStatus | string;
}

export interface TherapeuticArea {
  repository_id: string;
  name: string;
  slug?: string;
  owner_msl_id: string;
  default_flag: boolean;
  active?: boolean;
  enabled?: boolean;
  coming_soon?: boolean;
  sort_order?: number;
  description?: string;
  scope_keywords?: string[];
  msl_ids: string[];
  folders: Array<{ folder_id: string; name: string }>;
}

export interface PortalSession {
  user: PortalUser;
  session_id?: string;
  access_token?: string;
  recovery_codes?: string[];
}

/** First step of sign-in: either a session, or an MFA challenge / enrolment. */
export type LoginResult =
  | ({ kind: 'session' } & PortalSession)
  | { kind: 'mfa'; mfa_token: string }
  | { kind: 'mfa_enroll'; mfa_token: string };

export class ApiError extends Error {
  status: number;
  code?: string;
  userId?: string;
  requestId?: string;
  constructor(message: string, status: number, code?: string, userId?: string, requestId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.userId = userId;
    this.requestId = requestId;
  }
}

async function apiError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let code: string | undefined;
  let userId: string | undefined;
  let bodyRequestId: string | undefined;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') message = detail;
    else if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string') message = detail.message;
      code = detail.code;
      userId = detail.user_id;
    }
    if (typeof body?.message === 'string' && message.startsWith('Request failed')) message = body.message;
    code = code ?? body?.code;
    userId = userId ?? body?.user_id;
    bodyRequestId = typeof body?.request_id === 'string' ? body.request_id : undefined;
  } catch {
    // keep default message
  }
  const requestId = bodyRequestId || res.headers.get('x-request-id') || undefined;
  return new ApiError(message, res.status, code, userId, requestId);
}

export interface OpsStatus {
  ikm: {
    reachable: boolean;
    latency_ms: number | null;
    repository_count: number;
    detail: string;
    base_url: string;
  };
  llm_configured: boolean;
  molecules: string[];
  confidence_threshold: number;
  open_cases: number;
  cases: number;
  traces: number;
  outbox: number;
}

export function newConversationId(): string {
  const hex = crypto.randomUUID().replace(/-/g, '').slice(0, 16);
  return `conv_${hex}`;
}

export interface DoctorTherapeuticAreaPreference {
  repository_id: string;
  name: string;
  description?: string;
  is_default?: boolean;
}

export interface PortalMeResponse {
  user: PortalUser;
  therapeutic_areas: DoctorTherapeuticAreaPreference[];
  profile?: DoctorProfile | null;
  consents?: ConsentsState;
}

export async function fetchPortalMe(): Promise<PortalMeResponse> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PORTAL_ME}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function portalLogout(): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PORTAL_LOGOUT}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
  });
  if (!res.ok) throw new Error(await errorText(res));
}

export interface AuditEvent {
  event_id: string;
  created_at?: string;
  user_id: string;
  role: string;
  session_id: string;
  action: string;
  outcome: string;
  detail: Record<string, unknown>;
}

export async function listAuditEvents(params?: {
  sessionId?: string;
  action?: string;
  limit?: number;
  adminSessionId?: string;
  adminUserId?: string;
}): Promise<{ events: AuditEvent[]; total: number }> {
  const qs = new URLSearchParams();
  if (params?.sessionId) qs.set('session_id', params.sessionId);
  if (params?.action) qs.set('action', params.action);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.adminSessionId) qs.set('admin_session_id', params.adminSessionId);
  if (params?.adminUserId) qs.set('admin_user_id', params.adminUserId);
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_AUDIT_EVENTS}?${qs.toString()}`, {
    headers: portalAuthHeaders(),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function* askDigitalMsl(
  message: string,
  repositoryId: string,
  conversationId: string | undefined,
  history: { role: string; content: string }[],
  identity?: { userId?: string; assignedMslId?: string; sessionId?: string },
): AsyncGenerator<{ type: string; [key: string]: any }, void, unknown> {
  const res = await dmslFetch(
    `${API_BASE_URL}${API_ENDPOINTS.DMSL_WEB_MESSAGE}`,
    {
      method: 'POST',
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        message,
        repository_id: repositoryId,
        conversation_id: conversationId || undefined,
        session_id: identity?.sessionId || '',
        conversation_history: history,
        stream: true,
        user_id: identity?.userId || 'web_user',
        assigned_msl_id: identity?.assignedMslId || '',
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        client_utc_offset_minutes: new Date().getTimezoneOffset(),
      }),
    },
    180000,
  );
  if (!res.ok || !res.body) throw new Error(await errorText(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() || '';
    for (const event of events) {
      const line = event.trim();
      if (line.startsWith('data: ')) {
        try {
          yield JSON.parse(line.slice(6));
        } catch {
          // ignore malformed event
        }
      }
    }
  }
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TRACE(traceId)}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listTraces(limit = 50): Promise<TraceSummary[]> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TRACES}?limit=${limit}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).traces || [];
}

export async function listCases(filters?: {
  status?: string;
  doctorId?: string;
  assignedMslId?: string;
  viewerMslId?: string;
}): Promise<MslCase[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set('status', filters.status);
  if (filters?.doctorId) params.set('doctor_id', filters.doctorId);
  if (filters?.assignedMslId) params.set('assigned_msl_id', filters.assignedMslId);
  if (filters?.viewerMslId) params.set('viewer_msl_id', filters.viewerMslId);
  const qs = params.toString();
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASES}${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).cases || [];
}

export async function resolveCase(caseId: string, resolution: string): Promise<{ delivery?: Record<string, unknown> }> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_RESOLVE(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ resolution, reply_to_requester: true }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function getCase(caseId: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE(caseId)}`);
  if (!res.ok) throw new Error(await errorText(res));
  const data = await res.json();
  return data.case ?? data;
}

export async function getDmslConfig(): Promise<{
  molecules: string[];
  confidence_threshold: number;
  llm_configured: boolean;
  claim_min_score?: number;
  channels?: string[];
  ikm_base_url?: string;
}> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CONFIG}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function getOpsStatus(): Promise<OpsStatus> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_OPS_STATUS}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export interface OutboxMessage {
  outbox_id: string;
  message_id: string;
  correlation_id: string;
  channel: string;
  recipient: string;
  subject: string;
  body: string;
  delivery_status: string;
  provider_detail: string;
  created_at: string | null;
}

export async function listOutbox(params?: { channel?: string; limit?: number }): Promise<OutboxMessage[]> {
  const qs = new URLSearchParams();
  if (params?.channel) qs.set('channel', params.channel);
  if (params?.limit) qs.set('limit', String(params.limit));
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_OUTBOX}${qs.toString() ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(await errorText(res));
  const data = (await res.json()) as { messages: OutboxMessage[] };
  return data.messages || [];
}

export async function pollMailboxNow(): Promise<{ handled: number }> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_MAILBOX_POLL}`, { method: 'POST' });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function simulateWhatsAppInbound(payload: {
  from: string;
  text: string;
  id?: string;
  group_name?: string;
  group_id?: string;
  repository_id?: string;
}): Promise<AgentResponse> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_WHATSAPP_SIMULATE}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export interface WhatsAppDemoConfig {
  mock_mode: boolean;
  global_mock: boolean;
  business_display: string;
  note: string;
}

export async function fetchWhatsAppDemoConfig(): Promise<WhatsAppDemoConfig> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_WHATSAPP_DEMO_CONFIG}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function sendWhatsAppDemoMessage(payload: {
  text: string;
  from?: string;
  stream?: boolean;
  conversation_id?: string;
  conversation_history?: { role: string; content: string }[];
  repository_id?: string;
}): Promise<AgentResponse & { reply_text?: string }> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_WHATSAPP_DEMO_CHAT}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ ...payload, stream: false }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function* askWhatsAppDemo(
  message: string,
  options: {
    from?: string;
    conversationId?: string;
    history?: { role: string; content: string }[];
    repositoryId?: string;
  },
): AsyncGenerator<{ type: string; stage?: string; detail?: string; reply_text?: string; delivery?: { attachments?: string[] } & Record<string, unknown> } & Partial<Omit<AgentResponse, 'delivery'>>, void, unknown> {
  const res = await dmslFetch(
    `${API_BASE_URL}${API_ENDPOINTS.DMSL_WHATSAPP_DEMO_CHAT}`,
    {
      method: 'POST',
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        text: message,
        from: options.from,
        conversation_id: options.conversationId,
        conversation_history: options.history || [],
        repository_id: options.repositoryId || undefined,
        stream: true,
        id: `demo_${Date.now()}`,
      }),
    },
    180000,
  );
  if (!res.ok || !res.body) throw new Error(await errorText(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() || '';
    for (const event of events) {
      const line = event.trim();
      if (line.startsWith('data: ')) {
        try {
          yield JSON.parse(line.slice(6));
        } catch {
          /* ignore */
        }
      }
    }
  }
}

function toLoginResult(body: Record<string, unknown>): LoginResult {
  if (body.mfa_required) return { kind: 'mfa', mfa_token: String(body.mfa_token || '') };
  if (body.mfa_enrollment_required) return { kind: 'mfa_enroll', mfa_token: String(body.mfa_token || '') };
  return { kind: 'session', ...(body as unknown as PortalSession) };
}

async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function portalLogin(email: string, password: string): Promise<LoginResult> {
  return toLoginResult(await publicPost(API_ENDPOINTS.DMSL_PORTAL_LOGIN, { email, password }));
}

export async function portalLoginMfa(mfaToken: string, code: string): Promise<LoginResult> {
  return toLoginResult(await publicPost(API_ENDPOINTS.DMSL_LOGIN_MFA, { mfa_token: mfaToken, code }));
}

export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
}

/** `mfaToken` is set during forced enrolment at sign-in; omit it when enabling MFA from Settings (signed in). */
export async function mfaSetup(mfaToken?: string): Promise<MfaSetup> {
  if (mfaToken) return publicPost(API_ENDPOINTS.DMSL_MFA_SETUP, { mfa_token: mfaToken });
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_MFA_SETUP}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: '{}',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function mfaEnable(code: string, mfaToken?: string): Promise<LoginResult | { recovery_codes: string[] }> {
  if (mfaToken) return toLoginResult(await publicPost(API_ENDPOINTS.DMSL_MFA_ENABLE, { mfa_token: mfaToken, code }));
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_MFA_ENABLE}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ code }),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function portalRegister(body: DoctorRegisterPayload): Promise<RegisterResult> {
  return publicPost(API_ENDPOINTS.DMSL_REGISTER, body);
}

export async function verifyContact(userId: string, channel: 'email' | 'mobile', code: string): Promise<VerifyContactResult> {
  return publicPost(API_ENDPOINTS.DMSL_REGISTER_VERIFY, { user_id: userId, channel, code });
}

export async function resendContactCode(
  userId: string,
  channel: 'email' | 'mobile',
): Promise<{ otp_expires_in?: number; dev_codes?: { email?: string; mobile?: string } }> {
  return publicPost(API_ENDPOINTS.DMSL_REGISTER_RESEND, { user_id: userId, channel });
}

export async function fetchRegisterOptions(): Promise<RegisterOptions> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_REGISTER_OPTIONS}`);
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function logoutAllDevices(): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_LOGOUT_ALL}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
  });
  if (!res.ok) throw await apiError(res);
}

export async function updateConsents(
  partial: Partial<{ whatsapp_messaging: boolean; email_messaging: boolean }>,
): Promise<ConsentsState> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CONSENTS}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(partial),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export const CERTIFICATE_MAX_BYTES = 5 * 1024 * 1024;
export const CERTIFICATE_TYPES = ['application/pdf', 'image/png', 'image/jpeg'];

export function validateCertificateFile(file: File): string | null {
  if (!CERTIFICATE_TYPES.includes(file.type)) return 'Upload a PDF, PNG or JPEG file.';
  if (file.size > CERTIFICATE_MAX_BYTES) return 'The file must be 5 MB or smaller.';
  if (file.size === 0) return 'The file is empty.';
  return null;
}

export async function uploadCertificate(file: File): Promise<CertificateInfo> {
  const problem = validateCertificateFile(file);
  if (problem) throw new Error(problem);
  const form = new FormData();
  form.append('file', file);
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CERTIFICATE}`, {
    method: 'POST',
    headers: portalAuthHeaders(),
    body: form,
  }, 120000);
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export interface ComplianceNotice {
  version: string;
  text: string;
}

export async function fetchComplianceNotice(): Promise<ComplianceNotice> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_COMPLIANCE_NOTICE}`);
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export interface DoctorRegistryCounts {
  total: number;
  verified: number;
  pending: number;
  needs_info: number;
  rejected: number;
  [key: string]: number;
}

export interface DoctorListFilters {
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: 'asc' | 'desc';
  status?: string;
  repositoryId?: string;
  city?: string;
  q?: string;
}

export interface DoctorListResult {
  doctors: DoctorRegistryEntry[];
  counts: DoctorRegistryCounts;
  total: number;
  page: number;
  page_size: number;
}

export function doctorListQuery(filters?: DoctorListFilters): string {
  const params = new URLSearchParams();
  if (filters?.page) params.set('page', String(filters.page));
  if (filters?.pageSize) params.set('page_size', String(filters.pageSize));
  if (filters?.sort) params.set('sort', filters.sort);
  if (filters?.order) params.set('order', filters.order);
  if (filters?.status) params.set('status', filters.status);
  if (filters?.repositoryId) params.set('repository_id', filters.repositoryId);
  if (filters?.city) params.set('city', filters.city);
  if (filters?.q) params.set('q', filters.q);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export async function listDoctors(filters?: DoctorListFilters): Promise<DoctorListResult> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_DOCTORS}${doctorListQuery(filters)}`);
  if (!res.ok) throw await apiError(res);
  const body = await res.json();
  const doctors: DoctorRegistryEntry[] = body.doctors || [];
  return {
    doctors,
    counts: body.counts || { total: doctors.length, verified: 0, pending: 0, needs_info: 0, rejected: 0 },
    total: typeof body.total === 'number' ? body.total : doctors.length,
    page: body.page || filters?.page || 1,
    page_size: body.page_size || filters?.pageSize || doctors.length,
  };
}

export async function getDoctor(userId: string): Promise<DoctorRegistryEntry> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_DOCTOR(userId)}`);
  if (!res.ok) throw await apiError(res);
  return res.json();
}

async function downloadBlob(path: string, fallbackName: string): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${path}`, undefined, 120000);
  if (!res.ok) throw await apiError(res);
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = match ? decodeURIComponent(match[1]) : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export function exportDoctorsCsv(filters?: DoctorListFilters): Promise<void> {
  return downloadBlob(`${API_ENDPOINTS.DMSL_DOCTORS_EXPORT}${doctorListQuery({ ...filters, page: undefined, pageSize: undefined })}`, 'registered-doctors.csv');
}

export function downloadDoctorCertificate(userId: string): Promise<void> {
  return downloadBlob(API_ENDPOINTS.DMSL_DOCTOR_CERTIFICATE(userId), 'registration-certificate');
}

async function doctorAction(path: string, body: Record<string, unknown>): Promise<DoctorRegistryEntry> {
  const res = await dmslFetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export function setDoctorVerification(userId: string, status: VerificationStatus, reason = ''): Promise<DoctorRegistryEntry> {
  return doctorAction(API_ENDPOINTS.DMSL_DOCTOR_VERIFICATION(userId), { status, reason });
}

export function deactivateDoctor(userId: string, reason: string): Promise<DoctorRegistryEntry> {
  return doctorAction(API_ENDPOINTS.DMSL_DOCTOR_DEACTIVATE(userId), { reason });
}

export function reactivateDoctor(userId: string, reason = ''): Promise<DoctorRegistryEntry> {
  return doctorAction(API_ENDPOINTS.DMSL_DOCTOR_REACTIVATE(userId), { reason });
}

export interface SeedAreasResult {
  created: TherapeuticArea[];
  existing: string[];
  failed: Array<{ name: string; error: string }>;
}

export async function seedDefaultTherapeuticAreas(): Promise<SeedAreasResult> {
  const res = await dmslFetch(
    `${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_SEED_DEFAULTS}`,
    { method: 'POST', headers: jsonAuthHeaders() },
    300000,
  );
  if (!res.ok) throw await apiError(res);
  const body = await res.json();
  return { created: body.created || [], existing: body.existing || [], failed: body.failed || [] };
}

export interface TherapeuticAreaSettings {
  name?: string;
  description?: string;
  enabled?: boolean;
  sort_order?: number;
  scope_keywords?: string[];
  repository_id?: string;
}

export async function updateTherapeuticAreaSettings(key: string, body: TherapeuticAreaSettings): Promise<TherapeuticArea> {
  const res = await dmslFetch(
    `${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_SETTINGS(key)}`,
    { method: 'PUT', headers: jsonAuthHeaders(), body: JSON.stringify(body) },
    120000,
  );
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function listTherapeuticAreas(): Promise<TherapeuticArea[]> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_THERAPEUTIC_AREAS}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).therapeutic_areas || [];
}

export async function createTherapeuticArea(body: {
  name: string;
  description?: string;
  owner_msl_id?: string;
  default_flag?: boolean;
  msl_ids?: string[];
}): Promise<TherapeuticArea> {
  const res = await dmslFetch(
    `${API_BASE_URL}${API_ENDPOINTS.DMSL_THERAPEUTIC_AREAS}`,
    {
      method: 'POST',
      headers: jsonAuthHeaders(),
      body: JSON.stringify(body),
    },
    120000,
  );
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function deleteTherapeuticArea(repositoryId: string): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_THERAPEUTIC_AREA(repositoryId)}`, {
    method: 'DELETE',
    headers: jsonAuthHeaders(),
  });
  if (!res.ok) throw new Error(await errorText(res));
}

export async function setTherapeuticAreaMembers(
  repositoryId: string,
  mslIds: string[],
  ownerMslId?: string,
): Promise<TherapeuticArea> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_MEMBERS(repositoryId)}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ name: 'area', msl_ids: mslIds, owner_msl_id: ownerMslId || '' }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function createTherapeuticAreaFolder(
  repositoryId: string,
  name: string,
  parentFolderId = '',
): Promise<{ folder_id: string; name: string; parent_folder_id?: string }> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_FOLDERS(repositoryId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ name, parent_folder_id: parentFolderId }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function renameTherapeuticAreaFolder(folderId: string, name: string): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_FOLDER(folderId)}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await errorText(res));
}

export async function deleteTherapeuticAreaFolder(folderId: string): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_FOLDER(folderId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(await errorText(res));
}

export async function setTherapeuticAreaActive(repositoryId: string, active: boolean): Promise<TherapeuticArea> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_ACTIVE(repositoryId)}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ active }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function getFolderDocumentCounts(repositoryId: string): Promise<Record<string, number>> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_FOLDER_STATS(repositoryId)}`);
  if (!res.ok) throw new Error(await errorText(res));
  const body = await res.json();
  return body.document_counts || {};
}

export async function uploadTherapeuticAreaDocuments(
  repositoryId: string,
  files: File[],
  folderId = '',
): Promise<unknown> {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  if (folderId) form.append('folder_id', folderId);
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TA_UPLOAD(repositoryId)}`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function setDoctorDefaultTa(userId: string, repositoryId: string): Promise<PortalUser> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_DEFAULT_TA(userId)}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ repository_id: repositoryId }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function selfAssignCase(caseId: string, mslId: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_SELF_ASSIGN(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ msl_id: mslId }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listPortalUsers(role?: PortalUser['role']): Promise<PortalUser[]> {
  const qs = role ? `?role=${role}` : '';
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PORTAL_USERS}${qs}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).users || [];
}

export async function createPortalUser(body: {
  email: string;
  display_name: string;
  role: PortalUser['role'];
  assigned_msl_id?: string;
}): Promise<PortalUser & { temporary_password?: string }> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PORTAL_USERS}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function assignDoctorToMsl(doctorId: string, mslId: string): Promise<PortalUser> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PORTAL_ASSIGN}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ doctor_id: doctorId, msl_id: mslId }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listConversations(params?: {
  userId?: string;
  role?: string;
  doctorId?: string;
  status?: string;
  channel?: string;
  search?: string;
  sessionId?: string;
  limit?: number;
  offset?: number;
  adminSessionId?: string;
  adminUserId?: string;
  sessionScoped?: boolean;
}): Promise<{ conversations: ConversationSummary[]; total: number; limit: number; offset: number }> {
  const qs = new URLSearchParams();
  if (params?.sessionScoped) {
    if (params?.channel) qs.set('channel', params.channel);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset ?? 0));
  } else {
    if (params?.userId) qs.set('user_id', params.userId);
    if (params?.role) qs.set('role', params.role);
    if (params?.doctorId) qs.set('doctor_id', params.doctorId);
    if (params?.status) qs.set('status', params.status);
    if (params?.channel) qs.set('channel', params.channel);
    if (params?.search) qs.set('search', params.search);
    if (params?.sessionId) qs.set('session_id', params.sessionId);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    if (params?.adminSessionId) qs.set('admin_session_id', params.adminSessionId);
    if (params?.adminUserId) qs.set('admin_user_id', params.adminUserId);
  }
  const query = qs.toString();
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CONVERSATIONS}${query ? `?${query}` : ''}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listMyWebConversations(limit = 50): Promise<ConversationSummary[]> {
  const res = await listConversations({ channel: 'web', limit, sessionScoped: true });
  return res.conversations || [];
}

export async function getConversation(
  conversationId: string,
  userId?: string,
  role?: string,
  sessionScoped = false,
): Promise<ConversationDetail> {
  const qs = new URLSearchParams();
  if (!sessionScoped) {
    if (userId) qs.set('user_id', userId);
    if (role) qs.set('role', role);
  }
  const query = qs.toString();
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CONVERSATION(conversationId)}${query ? `?${query}` : ''}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function saveCaseDraft(
  caseId: string,
  draftResponse: string,
  references: MslCaseReference[],
  claims: MslCaseClaim[],
  authorId?: string,
): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_DRAFT(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      draft_response: draftResponse,
      references,
      claims,
      author_id: authorId || 'msl_default',
    }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function submitCaseReview(caseId: string, reviewerId?: string, notes?: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_SUBMIT_REVIEW(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ reviewer_id: reviewerId || 'msl_default', notes: notes || '' }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function approveCaseDraft(caseId: string, reviewerId?: string, notes?: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_APPROVE(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ reviewer_id: reviewerId || 'reviewer_default', notes: notes || '' }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function rejectCaseDraft(caseId: string, reviewerId?: string, notes?: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_REJECT(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ reviewer_id: reviewerId || 'reviewer_default', notes: notes || '' }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export interface PolicyMolecule {
  name: string;
  aliases: string;
}

export interface DmslPolicy {
  molecules: PolicyMolecule[];
  confidence_threshold: number;
  claim_min_score: number;
}

export interface MessageTemplate {
  template_id: string;
  kind: 'doctor_escalation_ack' | 'msl_new_case' | 'msl_response_to_doctor' | string;
  name: string;
  body: string;
  channel: 'all' | 'web' | 'email' | 'whatsapp' | string;
  updated_at?: string | null;
}

export async function getPolicy(): Promise<DmslPolicy> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_POLICY}`);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function savePolicy(policy: DmslPolicy): Promise<DmslPolicy> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_POLICY}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listTemplates(kind?: string): Promise<MessageTemplate[]> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TEMPLATES}${qs}`);
  if (!res.ok) throw new Error(await errorText(res));
  const data = await res.json();
  return data.templates || [];
}

export async function saveTemplate(template: Partial<MessageTemplate> & { kind: string; name: string; body: string }): Promise<MessageTemplate> {
  const hasId = Boolean(template.template_id);
  const url = hasId
    ? `${API_BASE_URL}${API_ENDPOINTS.DMSL_TEMPLATE(template.template_id as string)}`
    : `${API_BASE_URL}${API_ENDPOINTS.DMSL_TEMPLATES}`;
  const res = await fetch(url, {
    method: hasId ? 'PUT' : 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      template_id: template.template_id,
      kind: template.kind,
      name: template.name,
      body: template.body,
      channel: template.channel || 'all',
    }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function deleteTemplate(templateId: string): Promise<void> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_TEMPLATE(templateId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(await errorText(res));
}

export function renderTemplateBody(body: string, variables: Record<string, string>): string {
  return Object.entries(variables).reduce((acc, [key, value]) => acc.split(`{{${key}}}`).join(value ?? ''), body);
}

export async function sendCaseResponse(caseId: string, senderId?: string): Promise<MslCase> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_CASE_SEND_RESPONSE(caseId)}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ sender_id: senderId || 'msl_default', reply_to_requester: true }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export interface MslGroup {
  group_id: string;
  name: string;
  msl_ids: string[];
  doctor_ids: string[];
}

export interface PortalNotification {
  notification_id: string;
  kind: string;
  title: string;
  body: string;
  case_id: string;
  conversation_id?: string;
  read: boolean;
  created_at?: string | null;
}

export async function listGroups(): Promise<MslGroup[]> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_GROUPS}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).groups || [];
}

export async function createGroup(name: string): Promise<MslGroup> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_GROUPS}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function setGroupMembers(groupId: string, mslIds: string[]): Promise<MslGroup> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_GROUP_MEMBERS(groupId)}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ msl_ids: mslIds }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export interface DmslLibrary {
  repository_id: string;
  name: string;
}

export async function listDmslLibraries(): Promise<DmslLibrary[]> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_LIBRARIES}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).libraries || [];
}

export async function assignDoctorRepositories(doctorId: string, repositoryIds: string[]): Promise<PortalUser> {
  const headers = { 'Content-Type': 'application/json' };
  const attempts: Array<{ url: string; method: string; body: string }> = [
    {
      url: API_ENDPOINTS.DMSL_PORTAL_ASSIGN,
      method: 'POST',
      body: JSON.stringify({ doctor_id: doctorId, repository_ids: repositoryIds }),
    },
    {
      url: API_ENDPOINTS.DMSL_REPO_ASSIGN,
      method: 'POST',
      body: JSON.stringify({ doctor_id: doctorId, repository_ids: repositoryIds }),
    },
    {
      url: API_ENDPOINTS.DMSL_USER_REPOS(doctorId),
      method: 'PUT',
      body: JSON.stringify({ repository_ids: repositoryIds }),
    },
  ];
  let last: Response | null = null;
  for (const attempt of attempts) {
    last = await fetch(`${API_BASE_URL}${attempt.url}`, {
      method: attempt.method,
      headers,
      body: attempt.body,
    });
    if (last.ok) return last.json();
    if (![404, 405, 422].includes(last.status)) break;
  }
  throw new Error(await errorText(last!));
}

export async function assignDoctorGroup(doctorId: string, groupId: string): Promise<PortalUser> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_GROUP_ASSIGN}`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ doctor_id: doctorId, group_id: groupId }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function updatePortalProfile(
  userId: string,
  displayName?: string,
  password?: string,
  currentPassword?: string,
): Promise<PortalUser> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_PROFILE}`, {
    method: 'PUT',
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ user_id: userId, display_name: displayName, password, current_password: currentPassword }),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function listNotifications(userId: string, unreadOnly = false): Promise<PortalNotification[]> {
  const qs = new URLSearchParams({ user_id: userId, unread_only: unreadOnly ? 'true' : 'false' });
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_NOTIFICATIONS}?${qs}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).notifications || [];
}

export async function markNotificationsRead(userId: string, notificationId?: string): Promise<void> {
  const qs = new URLSearchParams({ user_id: userId });
  if (notificationId) qs.set('notification_id', notificationId);
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_NOTIFICATIONS_READ}?${qs}`, { method: 'POST' });
  if (!res.ok) throw new Error(await errorText(res));
}


// ---------------------------------------------------------------- /me/context
export interface MeContextArea {
  repository_id: string;
  name: string;
  description: string;
  is_default: boolean;
  enabled: boolean;
  coming_soon: boolean;
  answers_from_sources: boolean;
}

export interface MeContext {
  user: { user_id: string; display_name: string; role: PortalUser['role']; verification_status: string };
  verification_status: string;
  areas: MeContextArea[];
  default_repository_id: string;
  has_enabled_areas: boolean;
  routed_to_msl: boolean;
  knowledge_sources: { status: 'ok' | 'unavailable' | 'not_configured'; code?: string; message: string };
}

/** Everything the doctor chat needs in one call. The browser never lists repositories for a doctor. */
export async function fetchMeContext(): Promise<MeContext> {
  const res = await dmslFetch(`${API_BASE_URL}${API_ENDPOINTS.DMSL_ME_CONTEXT}`);
  if (!res.ok) throw await apiError(res);
  return res.json();
}


// ---------------------------------------------------------------- message actions
export interface AskMslResult {
  case_id: string;
  status: string;
  already_open: boolean;
}

/** Hand one message to a Physical MSL. Idempotent: asking twice returns already_open. */
export async function askMsl(messageId: string, reason?: string): Promise<AskMslResult> {
  const res = await dmslFetch(`${API_BASE_URL}/api/v1/digital-msl/messages/${encodeURIComponent(messageId)}/ask-msl`, {
    method: 'POST',
    headers: jsonAuthHeaders(),
    body: JSON.stringify(reason ? { reason } : {}),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** TODO: no backend endpoint yet; feedback is kept in local component state only. */
export async function sendFeedback(_messageId: string, _helpful: boolean): Promise<void> {
  return Promise.resolve();
}
