import React, { useEffect, useState, useCallback } from 'react';
import { Inbox, Clock, ArrowRight, ShieldCheck, AlertTriangle, UserCheck, CheckCircle2, FileText, RefreshCw, Search } from 'lucide-react';
import {
  listConversations,
  getConversation,
  type ConversationSummary,
  type ConversationDetail,
  type ClaimEvidence,
} from '../services/digitalMslService';
import { useToast } from '../contexts/ToastContext';

interface DoctorHistoryPanelProps {
  userId: string;
  onReopenConversation: (conversationId: string) => void;
}

function politeAnswer(text: string): string {
  return (text || '').replace(/\s*Case reference:\s*case_[A-Za-z0-9_-]+\.?/gi, '').trim();
}

function isOffTopicThread(detail: ConversationDetail): boolean {
  const questions = detail.messages.map((m) => (m.question || '').toLowerCase());
  if (questions.some((q) => /\bstatin\b|\bcholesterol\b/.test(q))) return true;
  return detail.messages.some(
    (m) => m.response?.decision === 'refuse' || (m.response?.reason_codes || []).includes('OUT_OF_SCOPE'),
  );
}

function followupKind(conv: ConversationSummary): 'waiting' | 'replied' | 'refused' | 'answered' {
  if (conv.status === 'refused') return 'refused';
  if (conv.status === 'resolved' || conv.status === 'sent') return 'replied';
  if (conv.has_escalation || conv.status === 'open' || conv.status === 'escalated') return 'waiting';
  return 'answered';
}

export const DoctorHistoryPanel: React.FC<DoctorHistoryPanelProps> = ({ userId, onReopenConversation }) => {
  const toast = useToast();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [activeEvidence, setActiveEvidence] = useState<ClaimEvidence | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listConversations({
        userId,
        role: 'doctor',
        status: filterStatus === 'all' || filterStatus === 'waiting' || filterStatus === 'refused' ? undefined : filterStatus,
        search: searchQuery.trim() || undefined,
        limit: 50,
      });
      let list = data.conversations;
      if (filterStatus === 'waiting') {
        list = list.filter((c) => followupKind(c) === 'waiting');
      } else if (filterStatus === 'resolved') {
        list = list.filter((c) => followupKind(c) === 'replied');
      }
      setConversations(list);
      if (list.length > 0 && !selectedId) setSelectedId(list[0].conversation_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not load follow-ups.');
    } finally {
      setLoading(false);
    }
  }, [userId, filterStatus, searchQuery, selectedId, toast]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const d = await getConversation(selectedId, userId, 'doctor');
        if (!cancelled) setDetail(d);
      } catch (err) {
        if (!cancelled) toast.error(err instanceof Error ? err.message : 'Could not load this follow-up.');
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, userId, toast]);

  const badge = (conv: ConversationSummary) => {
    const kind = followupKind(conv);
    if (kind === 'replied') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-selbg text-ok-fg border border-selb">
          <CheckCircle2 className="w-3 h-3" /> MSL replied
        </span>
      );
    }
    if (kind === 'refused') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-soft text-muted border border-line">
          Not a retina question
        </span>
      );
    }
    if (kind === 'waiting') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-warn-bg text-warn-fg border border-warn-line">
          <AlertTriangle className="w-3 h-3" /> Waiting for your MSL team
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-selbg text-ok-fg border border-selb">
        <ShieldCheck className="w-3 h-3" /> Answered
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-fg flex items-center gap-2">
            <Inbox className="w-5 h-5 text-link" />
            Follow-ups
          </h1>
          <p className="text-xs text-muted mt-1">Questions waiting for your MSL team, and replies you have already received.</p>
        </div>
        <button onClick={loadList} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-soft text-xs text-fg border border-line">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="lg:col-span-5 bg-card border border-line rounded-xl overflow-hidden flex flex-col h-[740px]">
          <div className="p-3.5 border-b border-line space-y-2.5">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                type="text"
                placeholder="Search your questions…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-card border border-line rounded-lg pl-8 pr-3 py-1.5 text-xs"
              />
            </div>
            <div className="flex gap-1.5">
              {[
                { id: 'all', label: 'All questions' },
                { id: 'waiting', label: 'Open follow-ups' },
                { id: 'resolved', label: 'Replied' },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setFilterStatus(tab.id)}
                  className={`text-[11px] px-2.5 py-1 rounded-md font-medium ${
                    filterStatus === tab.id ? 'bg-accent text-accentfg' : 'text-muted hover:bg-soft'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto divide-y divide-line">
            {conversations.map((conv) => (
              <button
                key={conv.conversation_id}
                onClick={() => setSelectedId(conv.conversation_id)}
                className={`w-full text-left p-4 ${selectedId === conv.conversation_id ? 'bg-selbg' : 'hover:bg-soft'}`}
              >
                <div className="flex items-center justify-between gap-2">
                  {badge(conv)}
                  <span className="text-[10px] text-muted flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {conv.last_activity ? new Date(conv.last_activity).toLocaleDateString() : ''}
                  </span>
                </div>
                <p className="mt-2 text-xs font-semibold text-fg line-clamp-2">{conv.latest_question}</p>
              </button>
            ))}
            {!loading && conversations.length === 0 && (
              <div className="p-8 text-center text-muted text-xs">
                <Inbox className="w-8 h-8 mx-auto mb-2 opacity-50" />
                No follow-ups yet.
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-7 bg-card border border-line rounded-xl overflow-hidden flex flex-col h-[740px]">
          {detail ? (
            <>
              <div className="p-4 border-b border-line flex items-center justify-between gap-3">
                <p className="text-xs text-muted">Started {detail.start_time ? new Date(detail.start_time).toLocaleString() : ''}</p>
                <button
                  onClick={() => onReopenConversation(detail.conversation_id)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-accentfg text-xs font-semibold"
                >
                  Continue in Talk to Digital MSL <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex-1 p-5 overflow-y-auto space-y-5">
                {detail.messages.map((msg, idx) => (
                  <div key={msg.message_id || idx} className="space-y-3">
                    {msg.question && (
                      <div className="flex justify-end">
                        <div className="max-w-[85%] rounded-2xl rounded-br-none p-3.5 bg-accent text-accentfg text-xs">
                          <p className="text-[10px] uppercase font-bold text-brandcyan mb-1">You</p>
                          <p className="whitespace-pre-wrap">{msg.question}</p>
                        </div>
                      </div>
                    )}
                    {msg.response && (
                      <div className="flex justify-start">
                        <div className={`max-w-[88%] rounded-2xl rounded-bl-none p-4 text-xs border ${msg.is_msl_response ? 'bg-selbg border-selb' : 'bg-soft border-line'}`}>
                          <p className="flex items-center gap-1.5 font-semibold text-link mb-2">
                            {msg.is_msl_response ? (
                              <>
                                <UserCheck className="w-3.5 h-3.5" /> Your MSL team
                              </>
                            ) : (
                              <>
                                <ShieldCheck className="w-3.5 h-3.5" /> Digital MSL
                              </>
                            )}
                          </p>
                          <div className="whitespace-pre-wrap text-fg">{politeAnswer(msg.response.answer)}</div>
                          {msg.response.claims?.length ? (
                            <div className="mt-3 pt-3 border-t border-line space-y-1">
                              <p className="text-[10px] font-bold uppercase text-muted">Sources</p>
                              {msg.response.claims.map((claim) =>
                                claim.evidence?.map((ev, i) => (
                                  <button
                                    key={`${claim.claim_id}-${i}`}
                                    onClick={() => setActiveEvidence(ev)}
                                    className="block text-[11px] text-link"
                                  >
                                    {ev.document_name}
                                    {ev.page_no ? ` · page ${ev.page_no}` : ''}
                                    {ev.line_start
                                      ? ev.line_end && ev.line_end !== ev.line_start
                                        ? ` · lines ${ev.line_start}–${ev.line_end}`
                                        : ` · line ${ev.line_start}`
                                      : ''}
                                  </button>
                                )),
                              )}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
                {detail.case &&
                  detail.case.status !== 'resolved' &&
                  detail.case.status !== 'sent' &&
                  !isOffTopicThread(detail) &&
                  detail.messages.every((m) => m.response?.decision !== 'refuse') && (
                  <div className="p-4 rounded-xl bg-warn-bg border border-warn-line text-warn-fg text-xs flex items-start gap-3">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <p>Your MSL team is reviewing this question. Their reply will appear here.</p>
                  </div>
                )}
              </div>
            </>
          ) : detailLoading ? (
            <div className="flex-1 flex items-center justify-center text-xs text-muted">Loading…</div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-muted text-xs">
              <Inbox className="w-10 h-10 mb-2 opacity-40" />
              Select a follow-up to read the conversation.
            </div>
          )}
        </div>
      </div>

      {activeEvidence && (
        <div className="fixed inset-0 z-50 bg-scrim flex items-center justify-center p-4">
          <div className="w-full max-w-xl bg-card rounded-2xl p-6 space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <FileText className="w-4 h-4 text-link" /> Source
            </div>
            <p className="text-xs">
              {activeEvidence.document_name}
              {activeEvidence.page_no ? ` · page ${activeEvidence.page_no}` : ''}
              {activeEvidence.line_start
                ? activeEvidence.line_end && activeEvidence.line_end !== activeEvidence.line_start
                  ? ` · lines ${activeEvidence.line_start}–${activeEvidence.line_end}`
                  : ` · line ${activeEvidence.line_start}`
                : ''}
            </p>
            {activeEvidence.paragraph && <p className="text-xs text-muted">{activeEvidence.paragraph}</p>}
            <p className="text-xs bg-soft rounded-lg p-3">{activeEvidence.original_text || activeEvidence.document_content}</p>
            <button onClick={() => setActiveEvidence(null)} className="text-xs px-3 py-1.5 bg-soft rounded-lg">Close</button>
          </div>
        </div>
      )}
    </div>
  );
};
