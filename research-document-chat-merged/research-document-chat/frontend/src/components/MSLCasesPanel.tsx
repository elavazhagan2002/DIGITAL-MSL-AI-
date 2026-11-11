import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Inbox, Mail, MessageCircle, Globe, RefreshCw, Send, Upload, X, ShieldCheck, UserCheck, Loader2, Trash2, ArrowLeft } from 'lucide-react';
import {
  approveCaseDraft,
  getCase,
  getConversation,
  listCases,
  listPortalUsers,
  listTemplates,
  renderTemplateBody,
  saveCaseDraft,
  selfAssignCase,
  sendCaseResponse,
  uploadTherapeuticAreaDocuments,
  type AgentResponse,
  type Claim,
  type ConversationDetail,
  type MessageTemplate,
  type MslCase,
  type MslCaseReference,
  type PortalUser,
  type TherapeuticArea,
} from '../services/digitalMslService';
import { useToast } from '../contexts/ToastContext';

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  web: <Globe className="w-3.5 h-3.5" />,
  email: <Mail className="w-3.5 h-3.5" />,
  whatsapp: <MessageCircle className="w-3.5 h-3.5" />,
};

function caseStatusLabel(status: string): string {
  if (status === 'open') return 'Open';
  if (status === 'assigned') return 'Assigned';
  if (status === 'in_progress') return 'In progress';
  if (status === 'resolved' || status === 'sent') return 'Resolved';
  return status;
}

function caseStatusClass(status: string): string {
  if (status === 'open') return 'bg-warn-bg text-warn-fg';
  if (status === 'assigned') return 'bg-selbg text-ok-fg';
  if (status === 'in_progress') return 'bg-selbg text-ok-fg';
  return 'bg-selbg text-ok-fg';
}

function digitalMslEngineText(response?: AgentResponse | null): string {
  if (!response) return '';
  if (response.decision === 'escalate_to_msl') {
    const draft = (response.presentation_body || '').trim();
    if (draft) return draft;
    const codes = (response.reason_codes || []).join(', ');
    return (
      `Digital MSL could not send an automatic answer (${codes || 'validation threshold'}). ` +
      'Review the evidence below and complete the reply for the requester.'
    );
  }
  return (response.answer || '').trim();
}

function fallbackCaseTranscript(c: MslCase): ConversationDetail {
  const post = (c.trace?.post_validation || {}) as { draft_answer?: string; claims?: Claim[] };
  const draft =
    (c.draft_response || '').trim() ||
    (post.draft_answer || '').trim() ||
    'Digital MSL processed this request but escalated it to a Physical MSL before sending a final answer.';
  const claimsFromTrace = (post.claims || []) as Claim[];
  const response: AgentResponse = {
    message_id: c.source_message_id || c.case_id,
    correlation_id: c.correlation_id,
    conversation_id: c.conversation_id || c.case_id,
    channel: c.channel as AgentResponse['channel'],
    status: 'escalated',
    decision: 'escalate_to_msl',
    answer: draft,
    presentation_body: draft,
    overall_score: typeof c.trace?.overall_score === 'number' ? (c.trace.overall_score as number) : 0,
    threshold: 0.9,
    claims: claimsFromTrace,
    reason_codes: c.reason_codes || [],
    trace_id: c.trace_id || '',
  };
  return {
    conversation_id: c.conversation_id || c.case_id,
    doctor_id: c.doctor_id || c.external_sender_id,
    doctor_name: c.external_sender_id || c.doctor_id || 'Requester',
    channel: c.channel,
    start_time: c.created_at,
    last_activity: c.updated_at || c.created_at,
    messages: [
      {
        message_id: c.source_message_id || c.case_id,
        correlation_id: c.correlation_id,
        question: c.question,
        received_at: c.created_at,
        status: 'escalated',
        channel: c.channel,
        is_msl_response: false,
        response,
      },
    ],
  };
}

interface MSLCasesPanelProps {
  role?: 'admin' | 'doctor' | 'msl';
  userId?: string;
  allowResolve?: boolean;
  areas?: TherapeuticArea[];
}

export const MSLCasesPanel: React.FC<MSLCasesPanelProps> = ({ role = 'admin', userId, allowResolve = true, areas = [] }) => {
  const toast = useToast();
  const [cases, setCases] = useState<MslCase[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<MslCase | null>(null);
  const [reply, setReply] = useState('');
  const [sending, setSending] = useState(false);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [transcript, setTranscript] = useState<ConversationDetail | null>(null);
  const [attachments, setAttachments] = useState<MslCaseReference[]>([]);
  const [taFilter, setTaFilter] = useState('all');
  const [mslNames, setMslNames] = useState<Record<string, string>>({});
  const evidenceInputRef = useRef<HTMLInputElement>(null);
  const [evidenceUploading, setEvidenceUploading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const filters =
        role === 'doctor' && userId
          ? { doctorId: userId }
          : role === 'msl' && userId
            ? { viewerMslId: userId }
            : undefined;
      const next = await listCases(filters);
      setCases(next.filter((c) => !(c.reason_codes || []).includes('OUT_OF_SCOPE')));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not load questions.');
    } finally {
      setLoading(false);
    }
  }, [toast, role, userId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    listPortalUsers('msl')
      .then((users: PortalUser[]) => {
        const map: Record<string, string> = {};
        users.forEach((u) => {
          map[u.user_id] = u.display_name;
        });
        setMslNames(map);
      })
      .catch(() => setMslNames({}));
  }, []);

  useEffect(() => {
    if (!allowResolve) return;
    listTemplates('msl_response_to_doctor')
      .then(setTemplates)
      .catch(() => undefined);
  }, [allowResolve]);

  const claim = async (c: MslCase) => {
    if (!userId) return;
    try {
      const updated = await selfAssignCase(c.case_id, userId);
      setCases((prev) => prev.map((item) => (item.case_id === updated.case_id ? updated : item)));
      setSelected(updated);
      toast.success('Request assigned to you.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not self-assign.');
    }
  };

  const select = async (c: MslCase) => {
    setSelected(c);
    setReply(c.draft_response || c.resolution || '');
    setAttachments(c.references || []);
    setTranscript(null);
    try {
      const detail = await getCase(c.case_id);
      setSelected(detail);
      if ((detail.draft_response || '').trim()) {
        setReply(detail.draft_response || '');
      } else {
        const post = detail.trace?.post_validation as { draft_answer?: string } | undefined;
        if ((post?.draft_answer || '').trim()) {
          setReply(post!.draft_answer!.trim());
        }
      }
      if (detail.references?.length) {
        setAttachments(detail.references);
      }
      if (detail.conversation?.messages?.length) {
        setTranscript(detail.conversation);
      } else {
        setTranscript(fallbackCaseTranscript(detail));
      }
    } catch {
      if (c.conversation_id || c.source_message_id) {
        try {
          const id = c.conversation_id || c.source_message_id!;
          setTranscript(await getConversation(id, userId, role === 'doctor' ? 'doctor' : 'msl'));
          return;
        } catch {
          /* fall through */
        }
      }
      setTranscript(fallbackCaseTranscript(c));
    }
  };

  const insertTemplate = () => {
    const tpl = templates[0];
    if (!tpl || !selected) return;
    setReply(
      renderTemplateBody(tpl.body, {
        doctor_name: selected.doctor_id || 'Doctor',
        question: selected.question || '',
        draft_body: reply,
        msl_name: 'MSL',
        case_id: selected.case_id,
      }),
    );
  };

  const send = async () => {
    if (!selected || !reply.trim() || !userId) return;
    setSending(true);
    try {
      await saveCaseDraft(selected.case_id, reply.trim(), attachments, selected.claims || [], userId);
      await approveCaseDraft(selected.case_id, userId, 'Sent from questions queue');
      await sendCaseResponse(selected.case_id, userId);
      toast.success('Reply sent to the doctor.');
      setSelected(null);
      setTranscript(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not send the reply.');
    } finally {
      setSending(false);
    }
  };

  const visibleCases = cases.filter((c) => taFilter === 'all' || c.repository_id === taFilter);
  const openCount = visibleCases.filter((c) => ['open', 'assigned', 'in_progress'].includes(c.status)).length;
  const mslUnassigned = role === 'msl' && areas.length === 0;

  const canWorkCase = (c: MslCase) =>
    allowResolve &&
    !['resolved', 'sent'].includes(c.status) &&
    c.draft_status !== 'sent' &&
    ['assigned', 'in_progress'].includes(c.status) &&
    c.assigned_msl_id === userId;

  const replyLocked = selected
    ? ['resolved', 'sent'].includes(selected.status) || selected.draft_status === 'sent'
    : false;

  const canEditReply = selected ? canWorkCase(selected) && !replyLocked : false;

  const onEvidenceFiles = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length || !selected) return;
    const repoId = selected.repository_id;
    setEvidenceUploading(true);
    try {
      if (repoId) {
        await uploadTherapeuticAreaDocuments(repoId, files);
      }
      setAttachments((prev) => [
        ...prev,
        ...files.map((file) => ({
          title: file.name,
          source: repoId ? `Uploaded to repository (${repoId})` : 'Evidence file (attach only)',
          is_external: true,
        })),
      ]);
      toast.success(repoId ? `Uploaded ${files.length} file(s) to the therapeutic area repository.` : `Added ${files.length} file(s) to evidence list.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Evidence upload failed.');
    } finally {
      setEvidenceUploading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      {mslUnassigned && (
        <div className="lg:col-span-5 rounded-xl border border-warn-line bg-warn-bg px-5 py-4 text-sm text-warn-fg">
          No therapeutic areas are assigned to your account. Ask an administrator to assign therapeutic areas before you can
          receive queue requests.
        </div>
      )}
      <div className="lg:col-span-2 bg-card border border-line rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-line flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-fg">Open Request</h2>
            <p className="text-xs text-muted">{openCount} active</p>
            <p className="text-[11px] text-muted mt-1 max-w-xs">
              Every doctor question for your therapeutic areas appears here. Self-assign one to reply.
            </p>
            {areas.length > 0 && (
              <select
                value={taFilter}
                onChange={(e) => setTaFilter(e.target.value)}
                aria-label="Filter by therapeutic area" className="mt-2 h-11 rounded-[10px] border-[1.5px] border-fieldb bg-field px-3 text-sm text-fg"
              >
                <option value="all">All therapeutic areas</option>
                {areas.map((area) => (
                  <option key={area.repository_id} value={area.repository_id}>
                    {area.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <button onClick={load} aria-label="Refresh queue" className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-line bg-soft text-fg">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        <ul className="max-h-[640px] overflow-y-auto divide-y divide-line">
          {visibleCases.map((c) => (
            <li key={c.case_id} className={`px-5 py-3 ${selected?.case_id === c.case_id ? 'bg-selbg' : ''}`}>
              <button
                type="button"
                onClick={() => select(c)}
                className="w-full text-left hover:opacity-90"
              >
                <div className="flex items-center gap-2 text-[11px] text-muted">
                  {CHANNEL_ICONS[c.channel]}
                  <span className={`px-1.5 rounded ${caseStatusClass(c.status)}`}>
                    {caseStatusLabel(c.status)}
                  </span>
                  <span className="ml-auto">{c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
                </div>
                <p className="mt-1 text-[11px] text-muted">{c.external_sender_id || c.doctor_id || 'Doctor'}</p>
                <p className="mt-0.5 text-xs text-fg line-clamp-2">{c.question}</p>
                {role === 'msl' && ['assigned', 'in_progress'].includes(c.status) && c.assigned_msl_id && c.assigned_msl_id !== userId && (
                  <p className="mt-1 text-[11px] font-medium text-warn-fg">
                    Already taken by {mslNames[c.assigned_msl_id] || 'another Physical MSL'}
                  </p>
                )}
                {role === 'msl' && ['assigned', 'in_progress'].includes(c.status) && c.assigned_msl_id === userId && (
                  <p className="mt-1 text-[11px] font-medium text-link">Assigned to you</p>
                )}
              </button>
              {role === 'msl' && c.status === 'open' && (
                <button
                  type="button"
                  className="mt-2 text-[11px] font-semibold px-2.5 py-1 rounded-md bg-accent text-accentfg"
                  onClick={() => claim(c)}
                >
                  Self-assign
                </button>
              )}
            </li>
          ))}
          {!loading && visibleCases.length === 0 && (
            <li className="px-5 py-10 text-center text-xs text-muted flex flex-col items-center gap-2">
              <Inbox className="w-8 h-8 stroke-1" />
              {mslUnassigned ? 'No queue items — therapeutic area assignment required.' : 'No questions waiting.'}
            </li>
          )}
        </ul>
      </div>

      <div className="lg:col-span-3 bg-card border border-line rounded-xl overflow-hidden">
        {selected ? (
          <div className="flex flex-col h-full max-h-[720px]">
            <div className="px-5 py-4 border-b border-line flex items-start justify-between gap-3">
              <div className="min-w-0">
                <button
                  type="button"
                  onClick={() => {
                    setSelected(null);
                    setTranscript(null);
                  }}
                  className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-link hover:underline"
                >
                  <ArrowLeft className="w-3.5 h-3.5" /> Back to queue
                </button>
                <h3 className="text-sm font-semibold text-fg">{selected.question}</h3>
                <p className="text-xs text-muted mt-1">From {selected.external_sender_id || selected.doctor_id || 'a doctor'}</p>
              </div>
              <button onClick={() => setSelected(null)} className="p-1 text-muted hover:bg-soft rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-4 text-xs">
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Scientific chat</p>
                {transcript?.messages?.length ? (
                  transcript.messages.map((msg, idx) => (
                    <div key={msg.message_id || idx} className="space-y-2">
                      {msg.question && (
                        <div className="rounded-xl bg-accent text-accentfg p-3">
                          <p className="text-[10px] uppercase font-bold text-brandcyan mb-1">Doctor</p>
                          <p className="whitespace-pre-wrap">{msg.question}</p>
                        </div>
                      )}
                      {msg.response && (
                        <div className="rounded-xl bg-soft border border-line p-3">
                          <p className="flex items-center gap-1 font-semibold text-link mb-1">
                            {msg.is_msl_response ? (
                              <>
                                <UserCheck className="w-3 h-3" /> MSL
                              </>
                            ) : (
                              <>
                                <ShieldCheck className="w-3 h-3" /> Digital MSL
                              </>
                            )}
                          </p>
                          <p className="whitespace-pre-wrap text-fg">
                            {digitalMslEngineText(msg.response)}
                          </p>
                          {msg.response?.decision === 'escalate_to_msl' && (msg.response.reason_codes?.length ?? 0) > 0 && (
                            <p className="mt-2 text-[11px] text-warn-fg">
                              Escalation: {(msg.response.reason_codes || []).join(', ')}
                            </p>
                          )}
                          {msg.response.claims?.flatMap((claim) => claim.evidence || []).length ? (
                            <ul className="mt-2 text-[11px] text-link space-y-0.5">
                              {msg.response.claims.flatMap((claim) =>
                                (claim.evidence || []).map((ev, i) => (
                                  <li key={`${claim.claim_id}-${i}`}>
                                    {ev.document_name} · page {ev.page_no}
                                    {ev.paragraph ? ` · ${ev.paragraph}` : ''}
                                  </li>
                                )),
                              )}
                            </ul>
                          ) : null}
                        </div>
                      )}
                    </div>
                  ))
                ) : (
                  <p className="text-muted">Loading Digital MSL context…</p>
                )}
              </div>
              {role === 'msl' && ['assigned', 'in_progress'].includes(selected.status) && selected.assigned_msl_id && selected.assigned_msl_id !== userId && (
                <p className="text-xs text-warn-fg bg-warn-bg border border-warn-line rounded-lg px-3 py-2">
                  Already taken by {mslNames[selected.assigned_msl_id] || 'another Physical MSL'}. You can read it; only they can reply.
                </p>
              )}
              {role === 'msl' && selected.status === 'open' && (
                <button type="button" onClick={() => claim(selected)} className="text-xs px-3 py-2 bg-accent text-accentfg rounded-lg font-semibold">
                  Self-assign
                </button>
              )}
              {replyLocked && (
                <p className="text-xs text-ok-fg bg-selbg border border-selb rounded-lg px-3 py-2">
                  Reply sent to the doctor. This response can no longer be edited.
                </p>
              )}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Your reply</p>
                  {allowResolve && templates.length > 0 && (
                    <button type="button" className="text-link" onClick={insertTemplate}>
                      Insert template
                    </button>
                  )}
                </div>
                <textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  disabled={!canEditReply}
                  rows={8}
                  className="w-full bg-soft border border-line rounded-lg p-3 text-fg focus:outline-none focus:ring-2 focus:ring-selb disabled:opacity-60 disabled:cursor-not-allowed"
                  placeholder="Write a clear scientific reply…"
                />
                {!canEditReply && selected.status !== 'sent' && selected.status !== 'resolved' && (
                  <p className="text-[11px] text-warn-fg mt-1">Self-assign this request to edit your reply.</p>
                )}
              </div>
              <div>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Evidence attachments</p>
                  <button
                    type="button"
                    disabled={!canEditReply || evidenceUploading}
                    onClick={() => evidenceInputRef.current?.click()}
                    className="text-xs px-3 py-1.5 rounded-md border border-line bg-card hover:bg-soft inline-flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {evidenceUploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                    Upload files
                  </button>
                  <input
                    ref={evidenceInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.docx,.doc,.txt"
                    className="hidden"
                    onChange={onEvidenceFiles}
                  />
                </div>
                <p className="text-[11px] text-muted mb-2">PDF, Word, or text files. Uploads go to the case therapeutic area repository when linked.</p>
                <ul className="space-y-1.5">
                  {attachments.map((item, idx) => (
                    <li key={`${item.title}-${idx}`} className="flex items-center justify-between gap-2 text-xs text-muted bg-soft border border-line rounded-md px-2 py-1.5">
                      <span className="truncate">
                        <span className="font-medium">{item.title}</span>
                        {item.source ? <span className="text-muted"> · {item.source}</span> : null}
                      </span>
                      {canEditReply && (
                        <button
                          type="button"
                          className="p-1 text-err-fg hover:bg-err-bg rounded"
                          aria-label="Remove attachment"
                          onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== idx))}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </li>
                  ))}
                  {!attachments.length && <li className="text-xs text-muted">No evidence files attached yet.</li>}
                </ul>
              </div>
            </div>
            {canEditReply && (
              <div className="p-4 border-t border-line flex justify-end">
                <button
                  onClick={send}
                  disabled={sending || !reply.trim()}
                  className="text-xs px-4 py-2 bg-accent hover:bg-accent disabled:bg-line text-accentfg rounded-md flex items-center gap-1.5"
                >
                  <Send className="w-3.5 h-3.5" /> Send reply
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="h-full min-h-[300px] flex items-center justify-center text-sm text-muted p-8 text-center">
            Select a request from the queue. Self-assign open items, then compose your reply.
          </div>
        )}
      </div>
    </div>
  );
};

export default MSLCasesPanel;
