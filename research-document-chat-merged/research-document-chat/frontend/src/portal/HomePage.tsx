import { useEffect, useState, type ReactNode } from 'react';
import { Inbox, MessageSquare, Users, FileText, Eye, BookOpen, Bell, Bot } from 'lucide-react';
import {
  listCases,
  listConversations,
  listNotifications,
  listOutbox,
  listPortalUsers,
  listTherapeuticAreas,
  markNotificationsRead,
  pollMailboxNow,
  simulateWhatsAppInbound,
  type MslCase,
  type ConversationSummary,
  type OutboxMessage,
  type PortalNotification,
} from '../services/digitalMslService';
import { useAuth, ROLE_LABEL } from './AuthContext';
import { useToast } from '../contexts/ToastContext';
import type { Repository } from '../services/repositoryService';

interface HomePageProps {
  repositories: Repository[];
  onOpen: (page: string) => void;
  onSelectRepo?: (id: string) => void;
}

export function HomePage({ repositories, onOpen, onSelectRepo }: HomePageProps) {
  const { user } = useAuth();
  const toast = useToast();
  const [cases, setCases] = useState<MslCase[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [convCount, setConvCount] = useState(0);
  const [peopleCount, setPeopleCount] = useState(0);
  const [taCount, setTaCount] = useState(0);
  const [notes, setNotes] = useState<PortalNotification[]>([]);
  const [bannerOpen, setBannerOpen] = useState(true);
  const [outbox, setOutbox] = useState<OutboxMessage[]>([]);
  const [mailboxPolling, setMailboxPolling] = useState(false);
  const [waFrom, setWaFrom] = useState('+919701038776');
  const [waText, setWaText] = useState(
    'In the 563-eye diabetic macular edema cohort, what percentage of eyes switched treatment?',
  );
  const [waGroup, setWaGroup] = useState('Digital MSL Scientific Support');
  const [waSending, setWaSending] = useState(false);
  const role = user?.role || 'doctor';

  useEffect(() => {
    if (!user) return;
    const filters =
      role === 'doctor'
        ? { doctorId: user.user_id }
        : role === 'msl'
          ? { viewerMslId: user.user_id }
          : undefined;
    listCases(filters).then(setCases).catch(() => setCases([]));
    listConversations({ userId: user.user_id, role, limit: 8 })
      .then((d) => {
        setConversations(d.conversations);
        setConvCount(d.total);
      })
      .catch(() => {
        setConversations([]);
        setConvCount(0);
      });
    if (role === 'admin') {
      listPortalUsers()
        .then((u) => setPeopleCount(u.length))
        .catch(() => setPeopleCount(0));
      listTherapeuticAreas()
        .then((areas) => setTaCount(areas.length))
        .catch(() => setTaCount(0));
      listOutbox({ channel: 'email', limit: 25 })
        .then(async (emailRows) => {
          const waRows = await listOutbox({ channel: 'whatsapp', limit: 25 }).catch(() => []);
          const merged = [...emailRows, ...waRows].sort(
            (a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime(),
          );
          setOutbox(merged);
        })
        .catch(() => setOutbox([]));
    }
    if (role === 'doctor' || role === 'msl') {
      listNotifications(user.user_id, true)
        .then((items) => {
          setNotes(items);
          const key = `dmsl_msl_notify_${user.user_id}`;
          if (role === 'msl' && items.some((n) => n.kind === 'new_question') && !sessionStorage.getItem(key)) {
            sessionStorage.setItem(key, '1');
            toast.success('You have new doctor questions waiting.');
          }
        })
        .catch(() => setNotes([]));
    }
  }, [user, role, toast]);

  const openWaiting = cases.filter((c) => c.status === 'open').length;
  const unreadQuestions = notes.filter((n) => n.kind === 'new_question' && !n.read);
  const unreadReplies = notes.filter((n) => n.kind === 'msl_reply' && !n.read);

  const openLibrary = (id: string) => {
    onSelectRepo?.(id);
    onOpen(role === 'doctor' ? 'chat' : 'documents');
  };

  const openAdminSettings = (tab: 'people' | 'areas') => {
    sessionStorage.setItem('dmsl_admin_settings_tab', tab);
    onOpen('settings');
  };

  const refreshOutbox = () => {
    Promise.all([
      listOutbox({ channel: 'email', limit: 25 }),
      listOutbox({ channel: 'whatsapp', limit: 25 }).catch(() => []),
    ])
      .then(([emailRows, waRows]) => {
        const merged = [...emailRows, ...waRows].sort(
          (a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime(),
        );
        setOutbox(merged);
      })
      .catch(() => setOutbox([]));
  };

  const runWhatsAppSimulate = async () => {
    setWaSending(true);
    try {
      const result = await simulateWhatsAppInbound({
        from: waFrom.trim(),
        text: waText.trim(),
        id: `wamid_sim_${Date.now()}`,
        group_name: waGroup.trim() || undefined,
      });
      toast.success(
        result.decision === 'escalate_to_msl'
          ? `Escalated to Physical MSL (case ${result.msl_case_id || '—'}). Check Questions queue.`
          : `Engine decision: ${result.decision}. Check delivery log.`,
      );
      refreshOutbox();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'WhatsApp simulate failed.');
    } finally {
      setWaSending(false);
    }
  };

  const runMailboxPoll = async () => {
    setMailboxPolling(true);
    try {
      const { handled } = await pollMailboxNow();
      toast.success(handled ? `Processed ${handled} inbox message(s).` : 'Inbox checked — no new questions to answer.');
      refreshOutbox();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Mailbox poll failed.');
    } finally {
      setMailboxPolling(false);
    }
  };

  const outboxStatusClass = (status: string) => {
    if (status === 'sent') return 'bg-selbg text-ok-fg';
    if (status === 'failed') return 'bg-err-bg text-err-fg';
    return 'bg-warn-bg text-warn-fg';
  };

  return (
    <div className="space-y-6">
      <div className="sciagen-hero relative overflow-hidden rounded-2xl px-6 py-7 sm:px-8">
        <div className="absolute inset-0 sciagen-grid opacity-30 pointer-events-none" />
        <div className="relative flex items-start gap-4">
          <div className="w-12 h-12 rounded-2xl bg-navyactive text-brandcyan flex items-center justify-center">
            <Eye className="w-6 h-6" />
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brandcyan">{ROLE_LABEL[role]} · Digital MSL</p>
            <h1 className="text-2xl sm:text-3xl font-semibold text-onnavy mt-1 tracking-tight">
              Welcome, <span className="text-brandcyan">{user?.display_name}</span>
            </h1>
            <p className="text-sm text-onnavymuted mt-1.5">
              {role === 'doctor' && 'Talk to Digital MSL from your therapeutic areas.'}
              {role === 'msl' && 'See open requests for your therapeutic areas and send a scientific reply.'}
              {role === 'admin' && 'Manage people, therapeutic areas, and the audit trail.'}
            </p>
          </div>
        </div>
      </div>

      {role === 'msl' && bannerOpen && unreadQuestions.length > 0 && (
        <div className="rounded-2xl border border-warn-line bg-gradient-to-r from-brandcyan to-brandcyan p-4 flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="flex items-start gap-3 flex-1">
            <div className="p-2 rounded-xl bg-warn-fg text-onnavy">
              <Bell className="w-4 h-4" />
            </div>
            <div>
              <p className="text-sm font-semibold text-warn-fg">
                {unreadQuestions.length} new doctor question{unreadQuestions.length === 1 ? '' : 's'}
              </p>
              <p className="text-xs text-warn-fg mt-0.5 line-clamp-2">{unreadQuestions[0].body}</p>
            </div>
          </div>
          <div className="flex gap-2">
            <button type="button" className="text-xs px-3 py-2 rounded-lg bg-warn-fg text-onnavy font-semibold" onClick={() => onOpen('cases')}>
              Open questions
            </button>
            <button
              type="button"
              className="text-xs px-3 py-2 rounded-lg border border-warn-line"
              onClick={async () => {
                if (user) await markNotificationsRead(user.user_id);
                setBannerOpen(false);
                setNotes([]);
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {role === 'doctor' && (
          <>
            <Tile color="cyan" title="Talk to Digital MSL" value="Open agent" hint="Therapeutic area science" onClick={() => onOpen('chat')} icon={<Bot className="w-5 h-5" />} />
            <Tile color="amber" title="Open follow-ups" value={String(openWaiting)} hint="Waiting for your MSL team" onClick={() => onOpen('history')} icon={<Inbox className="w-5 h-5" />} />
            <Tile color="blue" title="MSL replies" value={String(unreadReplies.length)} hint="New replies to read" onClick={() => onOpen('history')} icon={<Bell className="w-5 h-5" />} />
            <Tile color="indigo" title="Past questions" value={String(convCount)} hint="Your recent conversations" onClick={() => onOpen('history')} icon={<MessageSquare className="w-5 h-5" />} />
          </>
        )}
        {role === 'msl' && (
          <>
            <Tile color="amber" title="Waiting questions" value={String(openWaiting)} hint="Doctors in your team" onClick={() => onOpen('cases')} icon={<Inbox className="w-5 h-5" />} />
            <Tile color="rose" title="Unread alerts" value={String(unreadQuestions.length)} hint="New questions since last visit" onClick={() => onOpen('cases')} icon={<Bell className="w-5 h-5" />} />
            <Tile color="cyan" title="Therapeutic areas" value={String(repositories.length)} hint="Assigned collections" onClick={() => onOpen('literature')} icon={<BookOpen className="w-5 h-5" />} />
            <Tile color="indigo" title="Queue items" value={String(cases.length)} hint="All questions assigned to you" onClick={() => onOpen('cases')} icon={<MessageSquare className="w-5 h-5" />} />
          </>
        )}
        {role === 'admin' && (
          <>
            <Tile color="indigo" title="People" value={String(peopleCount || '—')} hint="Doctors, MSLs, and admins" onClick={() => openAdminSettings('people')} icon={<Users className="w-5 h-5" />} />
            <Tile color="cyan" title="Therapeutic areas" value={String(taCount || '—')} hint="Create areas and assign MSLs in Settings" onClick={() => openAdminSettings('areas')} icon={<FileText className="w-5 h-5" />} />
            <Tile color="blue" title="Audit Trail" value={String(convCount)} hint="Doctor sessions on file" onClick={() => onOpen('conversations')} icon={<MessageSquare className="w-5 h-5" />} />
            <Tile color="amber" title="Open MSL questions" value={String(openWaiting)} hint="Waiting for a human reply" onClick={() => onOpen('conversations')} icon={<Inbox className="w-5 h-5" />} />
          </>
        )}
      </div>

      {role === 'admin' && (
        <section className="bg-card border border-line rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-fg">Quick links</h2>
          <ul className="mt-3 flex flex-wrap gap-2 text-sm">
            <li>
              <button type="button" className="px-3 py-2 rounded-lg border border-line hover:bg-soft" onClick={() => openAdminSettings('people')}>
                Manage people
              </button>
            </li>
            <li>
              <button type="button" className="px-3 py-2 rounded-lg border border-line hover:bg-soft" onClick={() => openAdminSettings('areas')}>
                Therapeutic areas
              </button>
            </li>
            <li>
              <button type="button" className="px-3 py-2 rounded-lg border border-line hover:bg-soft" onClick={() => onOpen('conversations')}>
                Audit Trail
              </button>
            </li>
          </ul>
        </section>
      )}

      {role === 'admin' && (
        <section className="bg-card border border-line rounded-2xl p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-fg">Email & WhatsApp delivery log</h2>
              <p className="text-xs text-muted mt-1">
                Outbound replies on email and WhatsApp. MSLs still reply from the portal queue; WhatsApp group alerts use{' '}
                <code className="text-[10px]">DIGITAL_MSL_WHATSAPP_MSL_NOTIFY_PHONES</code> in backend .env.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={mailboxPolling}
                className="text-xs px-3 py-2 rounded-lg bg-accent text-accentfg font-semibold disabled:opacity-60"
                onClick={() => void runMailboxPoll()}
              >
                {mailboxPolling ? 'Checking inbox…' : 'Check inbox now'}
              </button>
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg border border-line hover:bg-soft"
                onClick={refreshOutbox}
              >
                Refresh log
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead>
                <tr className="text-muted border-b border-line">
                  <th className="py-2 pr-3 font-medium">Channel</th>
                  <th className="py-2 pr-3 font-medium">Time</th>
                  <th className="py-2 pr-3 font-medium">To</th>
                  <th className="py-2 pr-3 font-medium">Subject</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {outbox.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 text-muted">
                      No outbound messages yet. Test email or WhatsApp below, then refresh.
                    </td>
                  </tr>
                )}
                {outbox.map((row) => (
                  <tr key={row.outbox_id}>
                    <td className="py-2.5 pr-3 capitalize">{row.channel}</td>
                    <td className="py-2.5 pr-3 whitespace-nowrap text-muted">
                      {row.created_at ? new Date(row.created_at).toLocaleString() : '—'}
                    </td>
                    <td className="py-2.5 pr-3">{row.recipient}</td>
                    <td className="py-2.5 pr-3 max-w-xs truncate" title={row.subject}>
                      {row.subject}
                    </td>
                    <td className="py-2.5 pr-3">
                      <span className={`px-2 py-0.5 rounded-md font-semibold uppercase tracking-wide ${outboxStatusClass(row.delivery_status)}`}>
                        {row.delivery_status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="py-2.5 text-muted max-w-sm truncate" title={row.provider_detail}>
                      {row.provider_detail || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="border-t border-line pt-4 space-y-2">
            <h3 className="text-xs font-semibold text-fg">WhatsApp test message</h3>
            <p className="text-[11px] text-muted">
              Simulates a doctor DM to the business number. Escalations notify MSL phones in .env and appear in the MSL Questions queue.
            </p>
            <div className="grid sm:grid-cols-2 gap-2">
              <input
                type="text"
                value={waFrom}
                onChange={(e) => setWaFrom(e.target.value)}
                className="text-xs rounded-lg border border-line px-3 py-2 bg-soft"
                placeholder="Doctor WhatsApp +91…"
              />
              <input
                type="text"
                value={waGroup}
                onChange={(e) => setWaGroup(e.target.value)}
                className="text-xs rounded-lg border border-line px-3 py-2 bg-soft"
                placeholder="MSL group label"
              />
            </div>
            <textarea aria-label="WhatsApp demo message"
              value={waText}
              onChange={(e) => setWaText(e.target.value)}
              rows={3}
              className="w-full text-xs rounded-lg border border-line px-3 py-2 bg-soft"
            />
            <button
              type="button"
              disabled={waSending || !waFrom.trim() || !waText.trim()}
              className="text-xs px-3 py-2 rounded-lg bg-accent text-accentfg font-semibold disabled:opacity-60"
              onClick={() => void runWhatsAppSimulate()}
            >
              {waSending ? 'Sending…' : 'Simulate WhatsApp question'}
            </button>
          </div>
        </section>
      )}

      {(role === 'doctor' || role === 'msl') && (
        <section className="bg-card border border-line rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-fg">
            {role === 'doctor' ? 'Recent questions' : 'Recent waiting questions'}
          </h2>
          <ul className="mt-3 divide-y divide-line">
            {role === 'doctor' &&
              conversations.slice(0, 5).map((c) => (
                <li key={c.conversation_id}>
                  <button type="button" onClick={() => onOpen('history')} className="w-full text-left py-2.5 text-sm text-fg hover:text-link">
                    {c.latest_question}
                  </button>
                </li>
              ))}
            {role === 'msl' &&
              cases.slice(0, 5).map((c) => (
                <li key={c.case_id}>
                  <button type="button" onClick={() => onOpen('cases')} className="w-full text-left py-2.5 text-sm text-fg hover:text-link">
                    {c.question}
                  </button>
                </li>
              ))}
            {((role === 'doctor' && conversations.length === 0) || (role === 'msl' && cases.length === 0)) && (
              <li className="py-6 text-sm text-muted">Nothing here yet.</li>
            )}
          </ul>
        </section>
      )}

      {(role === 'doctor' || role === 'msl') && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-link" /> Therapeutic areas
            </h2>
            <button type="button" className="text-xs text-link" onClick={() => onOpen(role === 'doctor' ? 'chat' : 'literature')}>
              Open
            </button>
          </div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {repositories.map((repo) => (
              <button
                key={repo.repository_id}
                type="button"
                onClick={() => openLibrary(repo.repository_id)}
                className="text-left rounded-2xl p-4 bg-gradient-to-br from-selbg to-selbg border border-selb hover:border-selb"
              >
                <FileText className="w-5 h-5 text-link mb-2" />
                <p className="text-sm font-semibold text-fg">{repo.name}</p>
                <p className="text-[11px] text-muted mt-1 line-clamp-2">{repo.description || 'Approved therapeutic area'}</p>
              </button>
            ))}
            {repositories.length === 0 && (
              <p className="text-sm text-muted col-span-full py-8 text-center border border-dashed border-line rounded-2xl">No therapeutic areas yet.</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function Tile({
  title,
  value,
  hint,
  onClick,
  icon,
  color,
}: {
  title: string;
  value: string;
  hint: string;
  onClick: () => void;
  icon: ReactNode;
  color: 'cyan' | 'amber' | 'blue' | 'indigo' | 'rose';
}) {
  const styles: Record<string, string> = {
    cyan: 'from-brandcyan to-brandnavy',
    amber: 'from-brandcyan to-brandcyan',
    blue: 'from-brandcyan to-brandnavy',
    indigo: 'from-brandcyan to-brandnavy',
    rose: 'from-brandcyan to-brandnavy',
  };
  return (
    <button type="button" onClick={onClick} className="text-left rounded-2xl p-4 bg-card border border-line hover:shadow-md transition">
      <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${styles[color]} text-onnavy flex items-center justify-center mb-3`}>
        {icon}
      </div>
      <p className="text-xs font-medium text-muted">{title}</p>
      <p className="mt-1 text-2xl font-semibold text-fg">{value}</p>
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </button>
  );
}
