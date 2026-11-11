import React, { useEffect, useState, useCallback } from 'react';
import {
  MessageSquare,
  Search,
  Filter,
  Download,
  Clock,
  ShieldCheck,
  AlertTriangle,
  UserCheck,
  Bot,
  FileText,
  RefreshCw,
  ExternalLink,
  ChevronRight,
  X,
  Calendar,
  CheckCircle2,
  Layers,
} from 'lucide-react';
import {
  listConversations,
  getConversation,
  listAuditEvents,
  type AuditEvent,
  type ConversationSummary,
  type ConversationDetail,
} from '../services/digitalMslService';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from './AuthContext';

export const AdminConversationsPage: React.FC = () => {
  const toast = useToast();
  const { user, sessionId } = useAuth();
  const [view, setView] = useState<'sessions' | 'events'>('sessions');
  const [sessionFilter, setSessionFilter] = useState('');
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [channelFilter, setChannelFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listConversations({
        role: 'admin',
        search: search.trim() || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        channel: channelFilter === 'all' ? undefined : channelFilter,
        sessionId: sessionFilter.trim() || undefined,
        limit: 100,
      });
      setConversations(data.conversations);
      setTotalCount(data.total);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not load conversation list.');
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter, channelFilter, sessionFilter, sessionId, user?.user_id, toast]);

  const loadEvents = useCallback(async () => {
    setEventsLoading(true);
    try {
      const data = await listAuditEvents({
        sessionId: sessionFilter.trim() || undefined,
        limit: 200,
      });
      setAuditEvents(data.events);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not load audit events.');
    } finally {
      setEventsLoading(false);
    }
  }, [sessionFilter, sessionId, user?.user_id, toast]);

  useEffect(() => {
    if (view === 'events') loadEvents();
  }, [view, loadEvents]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const d = await getConversation(selectedId, undefined, 'admin');
        if (!cancelled) setDetail(d);
      } catch (err) {
        if (!cancelled) toast.error('Could not load conversation transcript.');
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, toast]);

  const exportTranscript = (format: 'csv') => {
    if (!detail) return;
    try {
      {
        const headers = ['MessageID', 'Timestamp', 'Sender', 'Text', 'Status', 'Score', 'Decision'];
        const rows = detail.messages.map((m) => [
          m.message_id,
          m.received_at || '',
          m.is_msl_response ? `MSL (${m.author_name || 'Liaison'})` : m.question ? 'Doctor' : 'Digital MSL Bot',
          `"${(m.question || m.response?.answer || '').replace(/"/g, '""')}"`,
          m.status,
          m.response?.overall_score ?? '',
          m.response?.decision ?? '',
        ]);
        const csvContent = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `conversation_${detail.conversation_id}.csv`;
        a.click();
        URL.revokeObjectURL(url);
      }
      toast.success(`Exported conversation transcript in ${format.toUpperCase()} format.`);
    } catch {
      toast.error('Failed to export conversation transcript.');
    }
  };

  const escalatedCount = conversations.filter((c) => c.has_escalation).length;
  const answeredCount = conversations.filter((c) => c.status === 'answered' || c.status === 'resolved').length;

  return (
    <div className="space-y-6">
      {/* Header bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-fg flex items-center gap-2">
            <MessageSquare className="w-5 h-5 text-brandcyan" />
            Audit Trail
          </h1>
          <p className="text-xs text-muted mt-1">
            Each doctor login is a session. Questions from that session are tracked together.
          </p>
        </div>
        <button
          onClick={() => (view === 'sessions' ? loadData() : loadEvents())}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-soft hover:bg-soft text-xs text-fg border border-line transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading || eventsLoading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setView('sessions')}
          className={`text-xs px-3 py-1.5 rounded-lg border ${
            view === 'sessions'
              ? 'bg-accent text-accentfg border-selb'
              : 'bg-card border-line text-muted'
          }`}
        >
          Sessions
        </button>
        <button
          type="button"
          onClick={() => setView('events')}
          className={`text-xs px-3 py-1.5 rounded-lg border ${
            view === 'events'
              ? 'bg-accent text-accentfg border-selb'
              : 'bg-card border-line text-muted'
          }`}
        >
          Event log
        </button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="p-4 rounded-xl bg-gradient-to-br from-selbg to-selbg border border-selb">
          <div className="flex items-center justify-between text-xs text-link font-medium">
            <span>Total</span>
            <Layers className="w-4 h-4" />
          </div>
          <p className="text-2xl font-bold text-fg mt-2">{totalCount}</p>
          <p className="text-[11px] text-muted mt-0.5">Doctor questions on file</p>
        </div>

        <div className="p-4 rounded-xl bg-gradient-to-br from-selbg to-selbg border border-selb">
          <div className="flex items-center justify-between text-xs text-link font-medium">
            <span>Resolved</span>
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <p className="text-2xl font-bold text-fg mt-2">{answeredCount}</p>
          <p className="text-[11px] text-muted mt-0.5">Checked against the therapeutic area</p>
        </div>

        <div className="p-4 rounded-xl bg-gradient-to-br from-brandcyan to-brandcyan border border-warn-line">
          <div className="flex items-center justify-between text-xs text-warn-fg font-medium">
            <span>Sent to MSL team</span>
            <AlertTriangle className="w-4 h-4" />
          </div>
          <p className="text-2xl font-bold text-fg mt-2">{escalatedCount}</p>
          <p className="text-[11px] text-muted mt-0.5">Waiting for a human reply</p>
        </div>
      </div>

      {/* Filter and search toolbar */}
      {view === 'sessions' && (
      <div className="p-4 rounded-xl bg-card border border-line flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder="Search by question or doctor name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-soft border border-line rounded-lg pl-9 pr-3 py-2 text-xs text-fg placeholder:text-muted focus:outline-none focus:border-selb transition"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted flex items-center gap-1">
            <Filter className="w-3.5 h-3.5" /> Status:
          </span>
          <select
            aria-label="Filter by outcome" value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-soft border border-line rounded-lg px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:border-selb"
          >
            <option value="all">All Outcomes</option>
            <option value="resolved">Resolved</option>
            <option value="escalated">Escalated</option>
            <option value="refused">Refused</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">Channel:</span>
          <select
            aria-label="Filter by channel" value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
            className="bg-soft border border-line rounded-lg px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:border-selb"
          >
            <option value="all">All Channels</option>
            <option value="web">Web Portal</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </select>
          <input
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
            placeholder="Session ID"
            className="bg-soft border border-line rounded-lg px-2.5 py-1.5 text-xs text-fg w-40"
          />
        </div>
      </div>
      )}

      {view === 'events' && (
        <div className="p-4 rounded-xl bg-card border border-line flex flex-wrap items-center gap-3">
          <input
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
            placeholder="Filter by session ID"
            className="bg-soft border border-line rounded-lg px-2.5 py-1.5 text-xs text-fg flex-1 min-w-[200px]"
          />
          <button type="button" onClick={loadEvents} className="text-xs px-3 py-1.5 bg-accent text-accentfg rounded-lg">
            Apply
          </button>
        </div>
      )}

      {view === 'events' ? (
        <div className="bg-card border border-line rounded-xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs text-fg">
              <thead className="bg-soft text-[11px] font-bold text-muted uppercase tracking-wider border-b border-line">
                <tr>
                  <th className="px-5 py-3.5">Time</th>
                  <th className="px-5 py-3.5">Action</th>
                  <th className="px-5 py-3.5">User</th>
                  <th className="px-5 py-3.5">Session</th>
                  <th className="px-5 py-3.5">Outcome</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {auditEvents.map((ev) => (
                  <tr key={ev.event_id} className="hover:bg-soft">
                    <td className="px-5 py-3 text-muted">{ev.created_at ? new Date(ev.created_at).toLocaleString() : '—'}</td>
                    <td className="px-5 py-3 font-medium">{ev.action}</td>
                    <td className="px-5 py-3">{ev.role} · {ev.user_id}</td>
                    <td className="px-5 py-3 font-mono text-[10px]">{ev.session_id || '—'}</td>
                    <td className="px-5 py-3">{ev.outcome || '—'}</td>
                  </tr>
                ))}
                {!eventsLoading && auditEvents.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-5 py-12 text-center text-muted">
                      No audit events yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
      <div className="bg-card border border-line rounded-xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-fg">
            <thead className="bg-soft text-[11px] font-bold text-muted uppercase tracking-wider border-b border-line">
              <tr>
                <th className="px-5 py-3.5">Doctor</th>
                <th className="px-5 py-3.5">Session</th>
                <th className="px-5 py-3.5">Latest Question</th>
                <th className="px-5 py-3.5">Channel</th>
                <th className="px-5 py-3.5">Turns</th>
                <th className="px-5 py-3.5">Last Activity</th>
                <th className="px-5 py-3.5">Status</th>
                <th className="px-5 py-3.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {conversations.map((conv) => (
                <tr
                  key={conv.conversation_id}
                  className={`hover:bg-soft transition ${
                    selectedId === conv.conversation_id ? 'bg-accent' : ''
                  }`}
                >
                  <td className="px-5 py-3.5">
                    <p className="font-semibold text-fg">{conv.doctor_name}</p>
                    <p className="text-[10px] text-muted font-mono">{conv.conversation_id}</p>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-[10px] text-muted">{conv.session_id || '—'}</td>
                  <td className="px-5 py-3.5 max-w-xs truncate text-fg" title={conv.latest_question}>
                    {conv.latest_question}
                  </td>
                  <td className="px-5 py-3.5 uppercase text-[10px] font-semibold text-muted">
                    {conv.channel}
                  </td>
                  <td className="px-5 py-3.5 font-semibold text-fg">{conv.message_count}</td>
                  <td className="px-5 py-3.5 text-muted text-[11px]">
                    {conv.last_activity ? new Date(conv.last_activity).toLocaleString() : 'N/A'}
                  </td>
                  <td className="px-5 py-3.5">
                    {conv.has_escalation ? (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-warn-fg text-warn-fg border border-warn-line">
                        Escalated
                      </span>
                    ) : conv.status === 'resolved' || conv.status === 'sent' ? (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-selbg text-ok-fg border border-selb">
                        Resolved
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-selbg text-ok-fg border border-selb">
                        Open
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <button
                      onClick={() => setSelectedId(conv.conversation_id)}
                      className="px-2.5 py-1 rounded bg-soft hover:bg-line text-ok-fg font-semibold text-[11px] inline-flex items-center gap-1 transition"
                    >
                      Open <ChevronRight className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && conversations.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-5 py-12 text-center text-muted">
                    No conversations matching the search criteria.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      )}

      {/* Detail Audit Drawer */}
      {selectedId && (
        <div className="fixed inset-0 z-50 bg-scrim backdrop-blur-sm flex justify-end">
          <div className="w-full max-w-3xl bg-card border-l border-line h-full flex flex-col shadow-2xl overflow-hidden">
            {/* Drawer Header */}
            <div className="p-5 border-b border-line bg-soft flex items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-bold text-fg">Conversation</h2>
                </div>
                <p className="text-xs text-muted mt-1">
                  Doctor: <span className="text-fg font-semibold">{detail?.doctor_name}</span>
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => exportTranscript('csv')}
                  className="px-2.5 py-1.5 rounded-lg bg-soft hover:bg-soft text-fg text-xs font-medium inline-flex items-center gap-1 border border-line transition"
                  title="Export CSV transcript"
                >
                  <Download className="w-3.5 h-3.5 text-brandcyan" /> CSV
                </button>
                <button
                  onClick={() => setSelectedId(null)}
                  className="p-1.5 text-muted hover:text-fg rounded-lg hover:bg-soft"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Drawer Body Timeline */}
            <div className="flex-1 p-6 overflow-y-auto space-y-6">
              {detailLoading ? (
                <div className="flex items-center justify-center h-48 text-xs text-muted gap-2">
                  <RefreshCw className="w-4 h-4 animate-spin text-brandcyan" /> Loading complete transcript…
                </div>
              ) : detail ? (
                detail.messages.map((m, idx) => (
                  <div key={m.message_id || idx} className="space-y-3">
                    {/* Doctor Query */}
                    {m.question && (
                      <div className="p-4 rounded-xl bg-soft border border-line space-y-1.5">
                        <div className="flex items-center justify-between text-[11px] text-brandcyan font-bold uppercase tracking-wider">
                          <span>Doctor Query (Inbound)</span>
                          <span className="text-muted font-normal">
                            {m.received_at ? new Date(m.received_at).toLocaleString() : ''}
                          </span>
                        </div>
                        <p className="text-xs text-fg whitespace-pre-wrap">{m.question}</p>
                      </div>
                    )}

                    {/* Bot or MSL Response */}
                    {m.response && (
                      <div
                        className={`p-4 rounded-xl border space-y-3 ${
                          m.is_msl_response
                            ? 'bg-selbg border-selb'
                            : 'bg-soft border-line'
                        }`}
                      >
                        <div className="flex items-center justify-between text-[11px]">
                          {m.is_msl_response ? (
                            <span className="inline-flex items-center gap-1.5 font-bold text-link">
                              <UserCheck className="w-3.5 h-3.5 text-link" />
                              Physical MSL Approved Resolution ({m.author_name || 'Liaison'})
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 font-bold text-brandcyan">
                              <Bot className="w-3.5 h-3.5 text-brandcyan" />
                              Digital MSL
                            </span>
                          )}
                          <span className="text-muted text-[10px]">
                            {m.received_at ? new Date(m.received_at).toLocaleString() : ''}
                          </span>
                        </div>

                        <div className="text-xs text-fg leading-relaxed whitespace-pre-wrap">
                          {m.response.answer}
                        </div>

                        {m.response.decision === 'refuse' && (
                          <p className="text-[11px] text-muted">Not a retina question — no MSL case opened.</p>
                        )}
                        {m.response.decision === 'escalate_to_msl' && (
                          <p className="text-[11px] text-warn-fg">Sent to the MSL team.</p>
                        )}

                        {/* Claims list */}
                        {m.response.claims && m.response.claims.length > 0 && (
                          <div className="p-3 bg-soft rounded-lg border border-line space-y-1.5">
                            <p className="text-[10px] font-bold uppercase text-muted">
                              Claim-Level Citations ({m.response.claims.length})
                            </p>
                            {m.response.claims.map((cl, i) => (
                              <div key={i} className="text-[11px] text-fg">
                                • {cl.claim_text}{' '}
                                {cl.evidence?.map((ev, ei) => (
                                  <span
                                    key={ei}
                                    className="px-1.5 py-0.2 rounded text-[10px] bg-accent text-brandcyan border border-selb ml-1"
                                  >
                                    {ev.document_name} (p.{ev.page_no})
                                  </span>
                                ))}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
