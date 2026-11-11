import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Bot,
  CalendarClock,
  FileText,
  Home,
  Inbox,
  Phone,
  Settings,
  ShieldAlert,
  Stethoscope,
} from 'lucide-react';
import { LoadDocumentsPage } from './components/loaddocs/LoadDocumentsPage';
import { DigitalMSLChat } from './components/DigitalMSLChat';
import { DocumentsPanel } from './components/DocumentsPanel';
import { MSLCasesPanel } from './components/MSLCasesPanel';
import { ErrorCard, LoadingCard, StateCard } from './components/StatePanels';
import { AdminConversationsPage } from './portal/AdminConversationsPage';
import type { Repository } from './services/repositoryService';
import {
  fetchPortalMe,
  listCases,
  listNotifications,
  markNotificationsRead,
  type DoctorProfile,
} from './services/digitalMslService';
import { ThemeProvider, useTheme } from './contexts/ThemeContext';
import { AuthProvider, ROLE_LABEL, useAuth } from './portal/AuthContext';
import { LoginPage } from './portal/LoginPage';
import { SettingsPage } from './portal/SettingsPage';
import { HomePage } from './portal/HomePage';
import { WhatsAppDemoChat } from './portal/WhatsAppDemoChat';
import { NotificationBell } from './portal/NotificationBell';
import { AppShell, Content, Footer } from './components/shell/AppShell';
import { Header } from './components/shell/Header';
import { Sidebar } from './components/shell/Sidebar';
import { DoctorRegistryPage } from './portal/DoctorRegistryPage';
import { VerificationPending } from './portal/VerificationPending';
import { isVerified, verificationInfo } from './utils/verificationStatus';
import { invalidate, clearQueryCache } from './lib/queryCache';
import { QUERY_KEYS, useMeContext, useRepositories, useTherapeuticAreaList } from './hooks/useAppData';
import { primaryButton } from './config/pageTheme';
import type { PortalUser } from './services/digitalMslService';

type Page = 'home' | 'doctors' | 'literature' | 'documents' | 'chat' | 'cases' | 'conversations' | 'settings' | 'whatsapp-demo';

const isTempRepository = (repo: Repository) => repo.name?.startsWith('_temp_');

function viewedReplyKey(userId: string) {
  return `dmsl_reply_viewed_${userId}`;
}

function readViewedReplies(userId: string): Set<string> {
  try {
    const raw = localStorage.getItem(viewedReplyKey(userId));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function rememberViewedReply(userId: string, notificationId: string) {
  if (!notificationId) return;
  const ids = readViewedReplies(userId);
  ids.add(notificationId);
  localStorage.setItem(viewedReplyKey(userId), JSON.stringify([...ids]));
}

const ICON = 'h-5 w-5 shrink-0';

const NAV: Record<PortalUser['role'], Array<{ id: Page; label: string; icon: ReactNode }>> = {
  admin: [
    { id: 'home', label: 'Home', icon: <Home className={ICON} aria-hidden="true" /> },
    { id: 'doctors', label: 'Registered Doctors', icon: <Stethoscope className={ICON} aria-hidden="true" /> },
    { id: 'whatsapp-demo', label: 'WhatsApp demo', icon: <Phone className={ICON} aria-hidden="true" /> },
    { id: 'conversations', label: 'Audit Trail', icon: <FileText className={ICON} aria-hidden="true" /> },
    { id: 'settings', label: 'Settings', icon: <Settings className={ICON} aria-hidden="true" /> },
  ],
  doctor: [
    { id: 'chat', label: 'Talk to Digital MSL', icon: <Bot className={ICON} aria-hidden="true" /> },
    { id: 'whatsapp-demo', label: 'WhatsApp demo', icon: <Phone className={ICON} aria-hidden="true" /> },
    { id: 'settings', label: 'Settings', icon: <Settings className={ICON} aria-hidden="true" /> },
  ],
  msl: [
    { id: 'cases', label: 'My Queue', icon: <Inbox className={ICON} aria-hidden="true" /> },
    { id: 'doctors', label: 'Registered Doctors', icon: <Stethoscope className={ICON} aria-hidden="true" /> },
    { id: 'literature', label: 'Load Documents', icon: <FileText className={ICON} aria-hidden="true" /> },
    { id: 'settings', label: 'Settings', icon: <Settings className={ICON} aria-hidden="true" /> },
  ],
};

const PAGE_TITLES: Record<Page, string> = {
  home: 'Home',
  doctors: 'Registered Doctors',
  literature: 'Load Documents',
  documents: 'Documents',
  chat: 'Talk to Digital MSL',
  cases: 'My Queue',
  conversations: 'Audit Trail',
  settings: 'Settings',
  'whatsapp-demo': 'WhatsApp demo',
};

function PortalApp() {
  const { user, sessionId, logout, setUser } = useAuth();
  const { theme, toggle } = useTheme();
  const [page, setPage] = useState<Page>('home');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRepoId, setSelectedRepoId] = useState('');
  const [showTaPopup, setShowTaPopup] = useState(false);
  const [mslQueueWelcome, setMslQueueWelcome] = useState<{ open: number; active: number } | null>(null);
  const [doctorMslWelcome, setDoctorMslWelcome] = useState<{ body: string; conversationId: string; notificationId: string } | null>(null);
  const [focusConversationId, setFocusConversationId] = useState('');
  const [chatMountKey, setChatMountKey] = useState(0);
  const [agentOpen, setAgentOpen] = useState(true);

  const [doctorProfile, setDoctorProfile] = useState<DoctorProfile | null>(null);
  const [meLoaded, setMeLoaded] = useState(false);
  const [meLoading, setMeLoading] = useState(false);

  const verificationStatus = user?.role === 'doctor' ? doctorProfile?.verification_status ?? user.verification_status : undefined;
  const doctorLocked = user?.role === 'doctor' && !isVerified(verificationStatus);

  // One shared request per key (lib/queryCache): a failure produces one inline message, never a pile of toasts.
  const isDoctor = user?.role === 'doctor';
  const isMsl = user?.role === 'msl';
  const meContext = useMeContext(isDoctor && !doctorLocked && user ? user.user_id : null);
  const mslAreasQuery = useTherapeuticAreaList(isMsl);
  const reposQuery = useRepositories(false); // Load Documents asks for its own context (load-documents/context)

  const refreshMe = useCallback(async () => {
    if (!user || user.role !== 'doctor') return;
    setMeLoading(true);
    try {
      const data = await fetchPortalMe();
      setDoctorProfile(data.profile ?? null);
      const next = {
        ...data.user,
        verification_status: data.profile?.verification_status ?? data.user.verification_status,
      };
      // Only replace the stored user when something changed, so identity-based effects do not re-run.
      if (JSON.stringify(next) !== JSON.stringify(user)) setUser(next);
    } catch {
      /* keep the stored profile; dmslFetch already handles expired sessions */
    } finally {
      setMeLoaded(true);
      setMeLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id, user?.role, setUser]);

  useEffect(() => {
    if (user?.role === 'doctor') refreshMe();
    else {
      setDoctorProfile(null);
      setMeLoaded(false);
      // Confirm the cookie session on load; a 401 returns the app to the login page.
      if (user) fetchPortalMe().catch(() => undefined);
    }
  }, [user?.user_id, user?.role, refreshMe]);

  useEffect(() => {
    if (!doctorLocked) return;
    const t = setInterval(refreshMe, 60000);
    return () => clearInterval(t);
  }, [doctorLocked, refreshMe]);

  // A new sign-in starts from a clean cache.
  useEffect(() => {
    if (!user) clearQueryCache();
  }, [user]);

  const nav = useMemo(() => {
    if (!user) return [];
    const items = NAV[user.role];
    return doctorLocked ? items.filter((item) => item.id !== 'whatsapp-demo') : items;
  }, [user, doctorLocked]);

  useEffect(() => {
    if (!user) return;
    if (user.role === 'doctor') {
      setPage('chat');
      setAgentOpen(true);
      setShowTaPopup(false);
      listNotifications(user.user_id, true)
        .then((items) => {
          const viewed = readViewedReplies(user.user_id);
          const reply = items.find((n) => n.kind === 'msl_reply' && !n.read && !viewed.has(n.notification_id));
          if (reply) {
            setDoctorMslWelcome({
              body: reply.body || 'Your MSL team sent a scientific reply.',
              conversationId: reply.conversation_id || '',
              notificationId: reply.notification_id,
            });
          }
        })
        .catch(() => undefined);
    } else if (user.role === 'msl') {
      setPage('cases');
      setShowTaPopup(true);
      setDoctorMslWelcome(null);
      listCases({ viewerMslId: user.user_id })
        .then((cases) => {
          const queue = cases.filter((c) => !(c.reason_codes || []).includes('OUT_OF_SCOPE'));
          const open = queue.filter((c) => c.status === 'open').length;
          const active = queue.filter((c) => ['open', 'assigned', 'in_progress'].includes(c.status)).length;
          setMslQueueWelcome({ open, active });
        })
        .catch(() => setMslQueueWelcome({ open: 0, active: 0 }));
    } else if (user.role === 'admin') {
      setPage('home');
      setShowTaPopup(false);
      setDoctorMslWelcome(null);
    }
  }, [user?.user_id]);

  useEffect(() => {
    if (!user) return;
    if (!nav.some((item) => item.id === page)) setPage(nav[0]?.id || 'home');
  }, [user, nav, page]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDrawerOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  if (!user) return <LoginPage />;

  const mslAreas = (mslAreasQuery.data ?? []).filter(
    (ta) => ta.active !== false && (ta.msl_ids.includes(user.user_id) || ta.owner_msl_id === user.user_id),
  );
  const mslRepos = (reposQuery.data ?? [])
    .filter((r) => !isTempRepository(r))
    .filter((r) => mslAreas.some((ta) => ta.repository_id === r.repository_id));
  const selectedRepo = mslRepos.find((r) => r.repository_id === selectedRepoId) || mslRepos[0];
  const activeRepoId = selectedRepo?.repository_id || '';

  const ctx = meContext.data;
  const doctorAreas = (ctx?.areas ?? []).filter((a) => a.enabled);
  const doctorRepositories: Repository[] = doctorAreas.map((a) => ({
    repository_id: a.repository_id,
    name: a.name,
    description: a.description,
  }));
  const doctorRepoId =
    doctorAreas.find((a) => a.repository_id === ctx?.default_repository_id)?.repository_id || doctorAreas[0]?.repository_id || '';

  const isWhatsAppDemo = page === 'whatsapp-demo';
  const go = (next: Page) => {
    setPage(next);
    setDrawerOpen(false);
    if (next === 'chat') {
      setFocusConversationId('');
      setChatMountKey((k) => k + 1);
      setAgentOpen(true);
    }
  };

  const doctorChat = () => {
    if (doctorLocked) {
      return (
        <VerificationPending
          status={verificationStatus}
          notes={doctorProfile?.rejection_reason || doctorProfile?.verification_notes}
          loading={meLoading || !meLoaded}
          onRefresh={refreshMe}
          onOpenSettings={() => go('settings')}
        />
      );
    }
    if (meContext.error && !ctx) {
      return <ErrorCard error={meContext.error} onRetry={meContext.refetch} retrying={meContext.loading} />;
    }
    if (!ctx) return <LoadingCard label="Loading your workspace…" />;
    if (ctx.areas.length === 0) {
      return (
        <StateCard
          testId="no-areas"
          icon={<Stethoscope className="h-6 w-6" />}
          title="Choose your therapeutic areas to start."
          text="Pick up to three areas in your profile. Your questions are answered within them."
          action={
            <button type="button" className={primaryButton} onClick={() => go('settings')}>
              Choose areas
            </button>
          }
        />
      );
    }
    if (!ctx.has_enabled_areas) {
      return (
        <StateCard
          testId="areas-coming-soon"
          icon={<CalendarClock className="h-6 w-6" />}
          title="Your therapeutic areas are coming soon."
          text={`${ctx.areas.map((a) => a.name).join(', ')} ${ctx.areas.length > 1 ? 'are' : 'is'} not open yet. You will be notified when ${ctx.areas.length > 1 ? 'they open' : 'it opens'}.`}
          action={
            <button type="button" className={primaryButton} onClick={() => go('settings')}>
              Review my areas
            </button>
          }
        />
      );
    }
    if (ctx.knowledge_sources.status === 'unavailable') {
      return (
        <ErrorCard
          error={{ message: ctx.knowledge_sources.message, code: ctx.knowledge_sources.code, retryable: true }}
          title="Digital MSL is temporarily unavailable"
          onRetry={meContext.refetch}
          retrying={meContext.loading}
        />
      );
    }
    return agentOpen ? (
      <DigitalMSLChat
        key={`dmsl-chat-${user.user_id}-${chatMountKey}-${focusConversationId || 'new'}`}
        repositoryId={doctorRepoId}
        repositoryName={doctorRepositories.map((r) => r.name).filter(Boolean).join(', ')}
        repositories={doctorRepositories}
        routedToMsl={ctx.routed_to_msl}
        userId={user.user_id}
        assignedMslId={user.assigned_msl_id}
        sessionId={sessionId}
        initialConversationId={focusConversationId || undefined}
        doctorDisplayName={user.display_name}
      />
    ) : null;
  };

  /** The chat owns the whole content area (its thread scrolls inside it); every other state scrolls normally. */
  const chatFill =
    page === 'chat' &&
    user.role === 'doctor' &&
    !doctorLocked &&
    !!ctx &&
    ctx.areas.length > 0 &&
    ctx.has_enabled_areas &&
    ctx.knowledge_sources.status !== 'unavailable';

  return (
    <>
      <AppShell
        sidebar={
          <Sidebar
            nav={nav}
            activeId={page}
            onSelect={(id) => go(id as Page)}
            userName={user.display_name}
            roleLabel={ROLE_LABEL[user.role]}
            userMeta={user.role === 'doctor' && verificationStatus ? `${ROLE_LABEL[user.role]} · ${verificationInfo(verificationStatus).label}` : undefined}
            onSignOut={logout}
            drawerOpen={drawerOpen}
            onCloseDrawer={() => setDrawerOpen(false)}
          />
        }
        scrim={drawerOpen ? <div className="fixed inset-0 z-40 bg-scrim shell:hidden" onClick={() => setDrawerOpen(false)} aria-hidden="true" /> : null}
      >
        {!isWhatsAppDemo && (
          <Header title={PAGE_TITLES[page]} theme={theme} onToggleTheme={toggle} onOpenMenu={() => setDrawerOpen(true)} menuOpen={drawerOpen}>
            {(user.role === 'doctor' || user.role === 'msl') && (
              <NotificationBell
                userId={user.user_id}
                role={user.role}
                suppressAutoPopup={(showTaPopup && user.role === 'msl') || !!doctorMslWelcome}
                onOpenCase={() => go(user.role === 'doctor' ? 'chat' : 'cases')}
              />
            )}
          </Header>
        )}

        {isWhatsAppDemo ? (
          <main className="min-h-0 flex-1 overflow-hidden" id="main-content">
            {(user.role === 'admin' || (user.role === 'doctor' && !doctorLocked)) && (
              <WhatsAppDemoChat key={`wa-demo-${user.user_id}-${page}`} userId={user.user_id} />
            )}
          </main>
        ) : (
          <Content fill={chatFill}>
            {page === 'home' && user.role === 'admin' && <HomePage repositories={[]} onOpen={(p) => go(p as Page)} />}
            {page === 'settings' && <SettingsPage />}
            {page === 'doctors' && (user.role === 'msl' || user.role === 'admin') && <DoctorRegistryPage />}
            {page === 'conversations' && user.role === 'admin' && <AdminConversationsPage />}
            {page === 'cases' && user.role === 'msl' && (
              <MSLCasesPanel role={user.role} userId={user.user_id} allowResolve areas={mslAreas} />
            )}

            {page === 'literature' && user.role === 'msl' && <LoadDocumentsPage role="msl" />}

            {page === 'chat' && user.role === 'doctor' && doctorChat()}
          </Content>
        )}
        {!isWhatsAppDemo && <Footer />}
      </AppShell>

      {showTaPopup && user.role === 'msl' && mslQueueWelcome && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-scrim p-4" onClick={() => setShowTaPopup(false)} role="presentation">
          <div
            className="w-full max-w-md space-y-4 rounded-2xl border border-line bg-card p-6 shadow-card"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="queue-welcome-title"
          >
            <h2 id="queue-welcome-title" className="text-base font-semibold text-fg">Doctor questions in your queue</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl border border-warn-line bg-warn-bg p-4 text-center">
                <p className="text-2xl font-bold text-warn-fg">{mslQueueWelcome.open}</p>
                <p className="mt-1 text-xs text-warn-fg">Open (self-assign)</p>
              </div>
              <div className="rounded-xl border border-selb bg-selbg p-4 text-center">
                <p className="text-2xl font-bold text-ok-fg">{mslQueueWelcome.active}</p>
                <p className="mt-1 text-xs text-ok-fg">Active in queue</p>
              </div>
            </div>
            <p className="text-sm text-muted">Select a request, self-assign if needed, then reply with evidence.</p>
            <button type="button" className={`${primaryButton} w-full`} onClick={() => setShowTaPopup(false)}>
              Go to My Queue
            </button>
          </div>
        </div>
      )}

      {doctorMslWelcome && user.role === 'doctor' && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-scrim p-4" onClick={() => setDoctorMslWelcome(null)} role="presentation">
          <div
            className="w-full max-w-md space-y-4 rounded-2xl border border-selb bg-card p-6 shadow-card"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="doctor-welcome-title"
          >
            <h2 id="doctor-welcome-title" className="text-lg font-semibold text-link">Good news, Doctor!</h2>
            <p className="text-base font-medium text-fg">Your MSL responded.</p>
            <p className="line-clamp-4 whitespace-pre-wrap text-sm text-muted">{doctorMslWelcome.body}</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="inline-flex h-11 items-center rounded-[10px] border-[1.5px] border-line px-4 text-sm font-semibold text-fg hover:bg-soft" onClick={() => setDoctorMslWelcome(null)}>
                Later
              </button>
              <button
                type="button"
                className={primaryButton}
                onClick={() => {
                  const conv = doctorMslWelcome.conversationId;
                  const noteId = doctorMslWelcome.notificationId;
                  rememberViewedReply(user.user_id, noteId);
                  setDoctorMslWelcome(null);
                  if (noteId) {
                    markNotificationsRead(user.user_id, noteId).catch(() => undefined);
                  }
                  if (conv) setFocusConversationId(conv);
                  go('chat');
                }}
              >
                View in chat
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export const App: React.FC = () => (
  <ThemeProvider>
    <AuthProvider>
      <PortalApp />
    </AuthProvider>
  </ThemeProvider>
);

export default App;
