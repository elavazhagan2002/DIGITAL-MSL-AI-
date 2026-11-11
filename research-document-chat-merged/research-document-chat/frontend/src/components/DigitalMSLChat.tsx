import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bot, ExternalLink, FileText, FolderTree, Send, ShieldCheck, X } from 'lucide-react';
import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import { askDigitalMsl, getConversation, listMyWebConversations, newConversationId, type AgentResponse, type ClaimEvidence, type ConversationSummary } from '../services/digitalMslService';
import type { Repository } from '../services/repositoryService';
import { SourcePdfViewer } from './SourcePdfViewer';
import { COMPOSER_DISCLAIMER, NoticeCard, NoticeModal, useComplianceNotice } from './chat/ChatNotice';
import { getAckedNoticeVersion, setAckedNoticeVersion } from '../lib/noticeAck';
import { AssistantMessage, AssistantNote } from './chat/MessageTypes';
import { friendlyError } from '../lib/friendlyError';
import { buildDoctorWelcomeGreeting } from '../utils/doctorGreeting';
import { formatDateTimeIST } from '../utils/formatDateTimeIST';

interface DigitalMSLChatProps {
  repositoryId: string;
  repositoryName?: string;
  repositories?: Repository[];
  /** answers are not drawn from approved sources right now: every question goes to a Physical MSL */
  routedToMsl?: boolean;
  /** Physical MSL / Administrator: may see the collapsible Details (scores and timing) */
  staff?: boolean;
  onSelectRepo?: (id: string) => void;
  userId?: string;
  assignedMslId?: string;
  sessionId?: string;
  initialConversationId?: string;
  doctorDisplayName?: string;
}

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  result?: AgentResponse;
  error?: string;
  clarify?: boolean;
}

const STAGE_LABELS: Record<string, string> = {
  received: 'Reading your question',
  scope: 'Checking this is in scope',
  retrieving: 'Searching the therapeutic area',
  evidence: 'Collecting supporting passages',
  drafting: 'Writing a draft',
  validating: 'Checking the draft',
  routing: 'Preparing the reply',
};

function refKey(ev: ClaimEvidence): string {
  return `${ev.document_id || ev.document_name}|${ev.page_no}|${ev.paragraph}|${ev.line_start || 0}`;
}

function politeAnswer(text: string): string {
  return (text || '').replace(/\s*Case reference:\s*case_[A-Za-z0-9_-]+\.?/gi, '').trim();
}

function stripSourcesFooter(text: string): string {
  return text
    .replace(/\n+Sources:\s*\n[\s\S]*$/i, '')
    .replace(/\n+Reference\n[\s\S]*$/i, '')
    .trim();
}

function documentRenderUrl(ev: ClaimEvidence): string | null {
  if (!ev.document_id) return null;
  const page = ev.page_no ? `#page=${ev.page_no}` : '';
  return `${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_RENDER(ev.document_id)}${page}`;
}

function confidencePct(result: AgentResponse): number {
  return Math.round(Math.max(0, Math.min(1, result.overall_score || 0)) * 100);
}

function formatDuration(ms?: number): string {
  if (!ms || ms < 0) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function verdictLabel(verdict?: string): string {
  if (verdict === 'partially_supported') return 'Partially supported';
  if (verdict === 'unsupported') return 'Unsupported';
  if (verdict === 'supported') return 'Supported by evidence';
  return 'Evidence review';
}

function AssistantAvatar() {
  return (
    <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-accent text-accentfg shrink-0">
      <Bot className="w-4 h-4" />
    </span>
  );
}

function convStorageKey(userId: string) {
  return `dmsl_active_conv_${userId}`;
}

function escalationRouteLabel(answer: string): string {
  const match = answer.match(/passed to (.+?) for review/i);
  if (match?.[1]) {
    return `Routed to ${match[1].trim()}`;
  }
  return 'Routed to your Physical MSL';
}

function detailToTurns(detail: Awaited<ReturnType<typeof getConversation>>): Turn[] {
  const loadedTurns: Turn[] = [];
  if (!detail?.messages) return loadedTurns;
  for (const m of detail.messages) {
    if (m.question) loadedTurns.push({ role: 'user', text: m.question });
    if (m.response) loadedTurns.push({ role: 'assistant', text: m.response.answer, result: m.response });
  }
  return loadedTurns;
}

export const DigitalMSLChat: React.FC<DigitalMSLChatProps> = ({
  repositoryId,
  repositoryName,
  repositories = [],
  routedToMsl = false,
  staff = false,
  onSelectRepo,
  userId,
  assignedMslId,
  sessionId,
  initialConversationId,
  doctorDisplayName,
}) => {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [citation, setCitation] = useState<ClaimEvidence | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<AgentResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [convList, setConvList] = useState<ConversationSummary[]>([]);
  const [convListLoading, setConvListLoading] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const skipScroll = useRef(true);
  const { notice } = useComplianceNotice();
  const [noticeModalOpen, setNoticeModalOpen] = useState(false);
  const [ackedVersion, setAckedVersion] = useState(() => getAckedNoticeVersion(userId || ''));
  // The notice is the first item of the thread until this doctor acknowledges the current version.
  const showNotice = !!notice && ackedVersion !== notice.version && !historyLoading;
  const ackPending = useRef(false);
  const acknowledgeNotice = () => {
    if (!notice) {
      ackPending.current = true; // sent before the notice loaded: acknowledge it as soon as it arrives
      return;
    }
    setAckedNoticeVersion(userId || '', notice.version);
    setAckedVersion(notice.version);
  };
  useEffect(() => {
    if (notice && ackPending.current) {
      ackPending.current = false;
      setAckedNoticeVersion(userId || '', notice.version);
      setAckedVersion(notice.version);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the notice arrives
  }, [notice]);

  const welcomeLines = buildDoctorWelcomeGreeting(doctorDisplayName);
  const showWelcome = turns.length === 0 && !busy && !historyLoading;

  useEffect(() => {
    if (skipScroll.current) {
      skipScroll.current = false;
      return;
    }
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  const refreshConvList = async () => {
    if (!userId) return;
    setConvListLoading(true);
    try {
      const items = await listMyWebConversations(50);
      setConvList(items);
    } catch {
      setConvList([]);
    } finally {
      setConvListLoading(false);
    }
  };

  const loadConversation = async (convId: string) => {
    if (!convId) return;
    setHistoryLoading(true);
    setConversationId(convId);
    if (userId) localStorage.setItem(convStorageKey(userId), convId);
    try {
      const detail = await getConversation(convId, undefined, undefined, true);
      skipScroll.current = true;
      setTurns(detailToTurns(detail));
    } catch {
      setTurns([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const startNewConversation = () => {
    skipScroll.current = true;
    setTurns([]);
    setHistoryLoading(false);
    const next = newConversationId();
    setConversationId(next);
    if (userId) localStorage.removeItem(convStorageKey(userId));
  };

  useEffect(() => {
    if (!userId) return;
    void refreshConvList();
    if (initialConversationId) {
      void loadConversation(initialConversationId);
      return;
    }
    startNewConversation();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fresh chat on portal open; history via sidebar only
  }, [userId]);

  useEffect(() => {
    skipScroll.current = true;
    if (!initialConversationId) return;
    void loadConversation(initialConversationId);
  }, [initialConversationId]);

  const sendQuestion = async (question: string) => {
    if (!question || busy || !repositoryId) return;
    const conv = conversationId || newConversationId();
    if (!conversationId) setConversationId(conv);
    acknowledgeNotice(); // sending the first message counts as reading the notice
    setInput('');
    const history = turns.map((t) => ({ role: t.role, content: t.result?.answer ?? t.text }));
    setTurns((prev) => [...prev, { role: 'user', text: question }]);
    setBusy(true);
    setStages([]);
    try {
      for await (const event of askDigitalMsl(question, repositoryId, conv, history, {
        userId,
        assignedMslId,
        sessionId,
      })) {
        if (event.type === 'status') {
          setStages((prev) => (prev.includes(event.stage) ? prev : [...prev, event.stage]));
        } else if (event.type === 'result') {
          const result = event as unknown as AgentResponse;
          if (result.conversation_id) setConversationId(result.conversation_id);
          setTurns((prev) => [...prev, { role: 'assistant', text: result.answer, result }]);
        } else if (event.type === 'error') {
          setTurns((prev) => [...prev, { role: 'assistant', text: '', error: event.detail }]);
        }
      }
    } catch (err) {
      setTurns((prev) => [...prev, { role: 'assistant', text: '', error: err instanceof Error ? err.message : 'Request failed.' }]);
    } finally {
      setBusy(false);
      setStages([]);
      void refreshConvList();
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    await sendQuestion(input.trim());
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 shell:flex-row" data-testid="chat-layout">
      <section
        aria-label="Chats"
        className="flex shrink-0 flex-col overflow-hidden rounded-2xl border border-line bg-card shadow-card shell:w-[260px]"
        data-testid="chat-history"
      >
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h2 className="text-sm font-semibold text-fg" data-testid="chats-heading">Chats</h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={startNewConversation}
              className="inline-flex h-11 items-center rounded-[10px] bg-accent px-4 text-sm font-semibold text-accentfg hover:opacity-90"
            >
              New
            </button>
            <button
              type="button"
              onClick={() => setHistoryOpen((o) => !o)}
              aria-expanded={historyOpen}
              aria-controls="chat-history-list"
              className="inline-flex h-11 items-center rounded-[10px] border-[1.5px] border-line px-3 text-sm font-semibold text-fg hover:bg-soft shell:hidden"
            >
              {historyOpen ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>
        <div
          id="chat-history-list"
          className={`${historyOpen ? 'block max-h-56' : 'hidden'} overflow-y-auto shell:block shell:max-h-none shell:flex-1`}
        >
          {convListLoading && <p className="p-3 text-xs text-muted animate-pulse">Loading…</p>}
          {!convListLoading && convList.length === 0 && (
            <p className="p-3 text-xs text-muted">No chats yet. Start a conversation.</p>
          )}
          {convList.map((c) => (
            <button
              key={c.conversation_id}
              type="button"
              onClick={() => loadConversation(c.conversation_id)}
              className={`w-full border-b border-line px-3 py-2.5 text-left hover:bg-soft ${
                conversationId === c.conversation_id ? 'bg-selbg' : ''
              }`}
            >
              <p className="line-clamp-2 text-xs font-medium text-fg">{c.latest_question || 'Conversation'}</p>
              <p className="mt-0.5 text-[10px] text-muted">
                {c.last_activity ? `${formatDateTimeIST(c.last_activity)} IST` : ''}
              </p>
            </button>
          ))}
        </div>
      </section>

      <section
        aria-label="Chat"
        className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-line bg-card shadow-card"
        data-testid="chat-panel"
      >
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-card px-6 py-3" data-testid="chat-area-header">
        <h2 className="min-w-0 max-w-full truncate text-sm font-semibold text-fg" data-testid="chat-area-name">
          {repositories[0]?.name || repositoryName?.split(',')[0]?.trim() || 'Your therapeutic area'}
        </h2>
        <span className="text-xs text-muted">
          {`Searching ${Math.max(repositories.length, 1)} ${repositories.length > 1 ? 'libraries' : 'library'}`}
        </span>
        <button
          type="button"
          onClick={() => setNoticeModalOpen(true)}
          data-testid="notice-link"
          className="ml-auto inline-flex h-11 items-center px-1 text-xs font-semibold text-link underline-offset-2 hover:underline"
        >
          Notice
        </button>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-6" data-testid="chat-thread">
        {historyLoading && (
          <div className="text-xs text-muted animate-pulse">Loading conversation…</div>
        )}

        {showNotice && notice && <NoticeCard notice={notice} routedToMsl={routedToMsl} onAcknowledge={acknowledgeNotice} />}

        {showWelcome && (
          <div className="flex items-start gap-3 max-w-lg">
            <AssistantAvatar />
            <div className="rounded-2xl rounded-bl-none px-5 py-4 text-sm bg-soft border border-line space-y-2">
              {welcomeLines.map((line, i) => (
                <p
                  key={i}
                  className={
                    i === 0
                      ? 'font-medium text-fg'
                      : 'text-muted leading-relaxed'
                  }
                >
                  {line}
                </p>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, idx) =>
          turn.role === 'user' ? (
            <div key={idx} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-none bg-accent px-5 py-3 text-sm text-accentfg">{turn.text}</div>
            </div>
          ) : turn.error ? (
            <div key={idx} className="flex items-start gap-3">
              <AssistantAvatar />
              <div className="space-y-2 rounded-lg border border-err-line bg-err-bg p-3 text-xs text-err-fg" role="alert">
                <p>{friendlyError(turn.error).message}</p>
                <button type="button" className="h-11 font-semibold text-link" onClick={() => sendQuestion(turns[idx - 1]?.text || input)}>
                  Retry
                </button>
              </div>
            </div>
          ) : turn.result ? (
            <AssistantMessage key={idx} result={turn.result} staff={staff} onEvidence={() => setEvidenceFor(turn.result || null)} />
          ) : (
            <AssistantNote key={idx} text={turn.text} />
          ),
        )}

        {busy && (
          <div className="text-xs bg-selbg border border-selb px-4 py-3 rounded-lg w-fit space-y-1.5">
            {Object.entries(STAGE_LABELS).map(([stage, label]) => {
              const done = stages.indexOf(stage);
              const current = done === stages.length - 1;
              if (done < 0) return null;
              return (
                <div key={stage} className={`flex items-center gap-2 ${current ? 'text-link animate-pulse' : 'text-muted'}`}>
                  {current ? <span className="w-2 h-2 rounded-full bg-accent" /> : <ShieldCheck className="w-3 h-3 text-link" />}
                  {label}
                </div>
              );
            })}
            {stages.length === 0 && <div className="text-link animate-pulse">Sending…</div>}
          </div>
        )}
      </div>

      <form onSubmit={submit} className="flex shrink-0 items-center gap-2 border-t border-line bg-soft p-4" data-testid="chat-composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy || !repositoryId}
          placeholder="What's in this repository?"
          aria-label="Your question" className="h-11 min-w-0 flex-1 rounded-[10px] border-[1.5px] border-fieldb bg-field px-4 text-sm text-fg placeholder-muted focus:outline-none focus:ring-2 focus:ring-selb"
        />
        <button
          type="submit"
          disabled={busy || !input.trim() || !repositoryId}
          aria-label="Send question" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] bg-accent text-accentfg hover:opacity-90 disabled:bg-line disabled:text-muted"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>

      <p className="shrink-0 bg-soft px-4 pb-3 text-center text-xs text-muted" data-testid="composer-disclaimer">
        {COMPOSER_DISCLAIMER}
      </p>

      <NoticeModal open={noticeModalOpen} notice={notice} onClose={() => setNoticeModalOpen(false)} />

      {evidenceFor && (
        <div className="absolute top-0 right-0 w-[min(100%,32rem)] h-full bg-card border-l border-line shadow-xl flex flex-col z-20">
          <div className="px-5 py-3 flex items-center justify-between border-b border-line">
            <span className="text-sm font-semibold">Evidence {confidencePct(evidenceFor)}%</span>
            <button onClick={() => setEvidenceFor(null)} className="p-1 text-muted">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 overflow-auto p-4 space-y-3">
            {evidenceFor.claims.map((claim, index) => (
              <details key={claim.claim_id} open className="rounded-xl border border-line p-3">
                <summary className="cursor-pointer text-sm font-medium">
                  Claim {index + 1}: {claim.claim_text}
                </summary>
                <p className="mt-2 text-[11px] uppercase tracking-wide text-link">
                  {verdictLabel(claim.validation?.verdict)}
                </p>
                <ul className="mt-2 space-y-2">
                  {claim.evidence.map((ev) => (
                    <li key={refKey(ev)} className="text-xs rounded-lg bg-soft p-2">
                      <p className="font-medium">{ev.document_name}</p>
                      <p className="text-muted">
                        original page {ev.page_no || '—'}
                        {ev.paragraph ? ` · ${ev.paragraph}` : ''}
                      </p>
                      {ev.original_text && <p className="mt-1 italic">“{ev.original_text}”</p>}
                      <button type="button" className="mt-1 text-link inline-flex items-center gap-1" onClick={() => setCitation({ ...ev, claim_text: claim.claim_text })}>
                        <FileText className="w-3 h-3" /> Open original paragraph
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        </div>
      )}

      {citation && (
        <div className="absolute top-0 right-0 w-[min(100%,48rem)] h-full bg-card border-l border-line shadow-xl flex flex-col z-30">
          <div className="px-5 py-3 flex items-center justify-between border-b border-line">
            <span className="flex items-center gap-2 text-link text-sm font-semibold min-w-0">
              <FileText className="w-4 h-4 shrink-0" />
              <span className="truncate">{citation.document_name || 'Source document'}</span>
              {citation.page_no ? <span className="text-xs font-normal text-muted shrink-0">original p. {citation.page_no}</span> : null}
            </span>
            <div className="flex items-center gap-1">
              {documentRenderUrl(citation) && (
                <a href={documentRenderUrl(citation) || undefined} target="_blank" rel="noreferrer" className="p-1 text-link rounded">
                  <ExternalLink className="w-4 h-4" />
                </a>
              )}
              <button onClick={() => setCitation(null)} className="p-1 text-muted rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
          {citation.document_id ? <SourcePdfViewer evidence={citation} /> : <p className="p-5 text-xs text-muted">This evidence is not linked to a source document.</p>}
        </div>
      )}
      </section>
    </div>
  );
};

export default DigitalMSLChat;
