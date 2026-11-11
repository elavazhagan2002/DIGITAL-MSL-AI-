import { useState } from 'react';
import { AlertTriangle, Bot, ChevronDown, ChevronUp, FolderTree, ShieldAlert, ThumbsDown, ThumbsUp } from 'lucide-react';
import { askMsl, sendFeedback, type AgentResponse, type ClaimEvidence } from '../../services/digitalMslService';
import { friendlyError } from '../../lib/friendlyError';
import { primaryButton, secondaryButton } from '../../config/pageTheme';

export const SENT_TO_MSL_TEXT = 'Sent to a Physical MSL. You will receive a reply here.';

type MessageKind = 'assistant_note' | 'answer' | 'escalation' | 'refusal' | 'safety';

/**
 * The kind is chosen ONLY by message_type. An answer without claims or without a retrieval run is never shown
 * as an answer (consistency rule): it falls back to a plain note.
 */
export function messageKind(result: AgentResponse): MessageKind {
  const t = result.message_type;
  if (t === 'answer') return result.retrieval_ran && (result.claims?.length ?? 0) > 0 ? 'answer' : 'assistant_note';
  if (t === 'escalation' || t === 'refusal' || t === 'safety') return t;
  return 'assistant_note';
}

function clean(text: string): string {
  return (text || '')
    .replace(/\s*Case reference:\s*case_[A-Za-z0-9_-]+\.?/gi, '')
    .replace(/\n+Sources:\s*\n[\s\S]*$/i, '')
    .replace(/\n+Reference\n[\s\S]*$/i, '')
    .trim();
}

function AssistantAvatar() {
  return (
    <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-accentfg" aria-hidden="true">
      <Bot className="h-4 w-4" />
    </span>
  );
}

const BUBBLE = 'max-w-2xl rounded-2xl rounded-bl-none border border-line bg-soft px-5 py-4 text-sm text-fg';

export function AssistantNote({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-3" data-testid="msg-assistant-note">
      <AssistantAvatar />
      <div className={BUBBLE}>
        <p className="whitespace-pre-wrap leading-relaxed">{clean(text)}</p>
      </div>
    </div>
  );
}

function useAskMsl(result: AgentResponse) {
  const [state, setState] = useState<'idle' | 'busy' | 'sent'>('idle');
  const [error, setError] = useState('');
  const run = async () => {
    if (state !== 'idle') return;
    setState('busy');
    setError('');
    try {
      await askMsl(result.message_id);
      setState('sent');
    } catch (err) {
      setError(friendlyError(err).message);
      setState('idle');
    }
  };
  return { state, error, run };
}

function AskMslControl({ result, primary }: { result: AgentResponse; primary?: boolean }) {
  const ask = useAskMsl(result);
  if (ask.state === 'sent') {
    return (
      <p className="text-sm font-medium text-ok-fg" role="status" data-testid="sent-to-msl">
        {SENT_TO_MSL_TEXT}
      </p>
    );
  }
  return (
    <div className="space-y-1">
      <button
        type="button"
        className={`${primary ? primaryButton : secondaryButton} h-11`}
        onClick={ask.run}
        disabled={ask.state === 'busy'}
      >
        {primary ? 'Send to a Physical MSL' : 'Ask a Physical MSL'}
      </button>
      {ask.error ? <p className="text-xs text-err-fg" role="alert">{ask.error}</p> : null}
    </div>
  );
}

function Citation({ ev }: { ev: ClaimEvidence & { version?: string; effective_date?: string; title?: string } }) {
  const parts = [
    ev.title || ev.document_name,
    ev.version ? `v${ev.version}` : '',
    ev.section || '',
    ev.page_no ? `p. ${ev.page_no}` : '',
    ev.effective_date ? `effective ${ev.effective_date}` : '',
  ].filter(Boolean);
  return <p className="text-xs text-muted">{parts.join(' · ')}</p>;
}

export function MetricsDetails({ result }: { result: AgentResponse }) {
  const [open, setOpen] = useState(false);
  const m = result.metrics;
  // Only when retrieval ran, there are sources and metrics exist.
  if (!m || !result.retrieval_ran || !(result.claims?.length > 0)) return null;
  const pct = (n: number) => `${Math.round(Math.max(0, Math.min(1, n)) * 100)}%`;
  return (
    <div className="text-xs" data-testid="metrics-details">
      <button
        type="button"
        className="inline-flex h-11 items-center gap-1 font-semibold text-link"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        Details {open ? <ChevronUp className="h-3 w-3" aria-hidden="true" /> : <ChevronDown className="h-3 w-3" aria-hidden="true" />}
      </button>
      {open && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-lg border border-line bg-card p-3 text-fg">
          <dt className="text-muted">Retrieval confidence</dt>
          <dd>{pct(m.retrieval_confidence)}</dd>
          <dt className="text-muted">Groundedness</dt>
          <dd>{pct(m.groundedness)}</dd>
          <dt className="text-muted">Response time</dt>
          <dd>{m.response_time_ms < 1000 ? `${m.response_time_ms} ms` : `${(m.response_time_ms / 1000).toFixed(1)} s`}</dd>
        </dl>
      )}
    </div>
  );
}

export function AnswerMessage({
  result,
  staff,
  onEvidence,
}: {
  result: AgentResponse;
  staff: boolean;
  onEvidence: () => void;
}) {
  const [vote, setVote] = useState<boolean | null>(null);
  const vote_ = (helpful: boolean) => {
    setVote(helpful);
    void sendFeedback(result.message_id, helpful);
  };
  const provenance = (result as AgentResponse & { provenance?: string }).provenance;
  return (
    <div className="flex items-start gap-3" data-testid="msg-answer">
      <AssistantAvatar />
      <div className="w-full max-w-2xl space-y-3 rounded-2xl rounded-bl-none border border-line bg-soft p-4">
        {result.claims.map((claim) => (
          <figure key={claim.claim_id} className="space-y-1 border-l-[3px] border-selb pl-3">
            <blockquote className="text-sm leading-relaxed text-fg">{claim.claim_text}</blockquote>
            {claim.evidence.map((ev, i) => (
              <Citation key={`${claim.claim_id}-${i}`} ev={ev} />
            ))}
          </figure>
        ))}
        {provenance ? <p className="text-xs text-muted">{provenance}</p> : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <span className="text-xs text-muted">Was this helpful?</span>
          <button
            type="button"
            aria-label="Helpful"
            aria-pressed={vote === true}
            onClick={() => vote_(true)}
            className={`flex h-11 w-11 items-center justify-center rounded-[10px] border border-line ${vote === true ? 'bg-selbg text-ok-fg' : 'bg-card text-fg'}`}
          >
            <ThumbsUp className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Not helpful"
            aria-pressed={vote === false}
            onClick={() => vote_(false)}
            className={`flex h-11 w-11 items-center justify-center rounded-[10px] border border-line ${vote === false ? 'bg-selbg text-ok-fg' : 'bg-card text-fg'}`}
          >
            <ThumbsDown className="h-4 w-4" aria-hidden="true" />
          </button>
          <AskMslControl result={result} />
          {staff && (
            <button type="button" onClick={onEvidence} className={`${secondaryButton} h-11`}>
              <FolderTree className="h-4 w-4" aria-hidden="true" /> Sources
            </button>
          )}
        </div>
        {staff && <MetricsDetails result={result} />}
      </div>
    </div>
  );
}

export function EscalationNotice({ result }: { result: AgentResponse }) {
  const answered = result.status === 'answered' || result.status === 'resolved';
  return (
    <div className="flex items-start gap-3" data-testid="msg-escalation">
      <AssistantAvatar />
      <div className="max-w-2xl rounded-2xl rounded-bl-none border border-warn-line bg-warn-bg px-5 py-4 text-sm text-warn-fg">
        <p className="whitespace-pre-wrap leading-relaxed">{clean(result.answer)}</p>
        {!/Physical MSL/i.test(result.answer || '') && (
          <p className="mt-2 leading-relaxed">Your question will be reviewed by a Physical MSL.</p>
        )}
        {result.msl_case_id ? (
          <span className="mt-2 inline-flex items-center gap-1 rounded-full border border-warn-line bg-card px-2.5 py-0.5 text-xs font-medium text-fg" data-testid="case-chip">
            <AlertTriangle className="h-3 w-3" aria-hidden="true" /> {answered ? 'Answered' : 'Open'}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function RefusalOrSafetyNotice({ result, safety }: { result: AgentResponse; safety: boolean }) {
  const canSend = (result.actions ?? []).includes('send_to_msl');
  return (
    <div className="flex items-start gap-3" data-testid={safety ? 'msg-safety' : 'msg-refusal'}>
      <AssistantAvatar />
      <div className={`max-w-2xl space-y-3 rounded-2xl rounded-bl-none border px-5 py-4 text-sm ${safety ? 'border-err-line bg-err-bg text-err-fg' : 'border-line bg-soft text-fg'}`}>
        <p className="flex items-start gap-2 whitespace-pre-wrap leading-relaxed">
          {safety ? <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> : null}
          <span>{clean(result.answer)}</span>
        </p>
        {canSend ? <AskMslControl result={result} primary /> : null}
      </div>
    </div>
  );
}

/** Chooses the component from message_type alone. */
export function AssistantMessage({
  result,
  staff,
  onEvidence,
}: {
  result: AgentResponse;
  staff: boolean;
  onEvidence: () => void;
}) {
  switch (messageKind(result)) {
    case 'answer':
      return <AnswerMessage result={result} staff={staff} onEvidence={onEvidence} />;
    case 'escalation':
      return <EscalationNotice result={result} />;
    case 'refusal':
      return <RefusalOrSafetyNotice result={result} safety={false} />;
    case 'safety':
      return <RefusalOrSafetyNotice result={result} safety />;
    default:
      return <AssistantNote text={result.answer} />;
  }
}
