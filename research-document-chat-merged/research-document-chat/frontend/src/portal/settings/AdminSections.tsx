import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Loader2, RefreshCw, Search } from 'lucide-react';
import { API_BASE_URL } from '../../config/api';
import {
  createPortalUser,
  deactivateDoctor,
  fetchComplianceNotice,
  getDmslConfig,
  getOpsStatus,
  listAuditEvents,
  listPortalUsers,
  reactivateDoctor,
  type AuditEvent,
  type ComplianceNotice,
  type OpsStatus,
  type PortalUser,
} from '../../services/digitalMslService';
import { ROLE_LABEL } from '../AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { inputClass, primaryButton, secondaryButton, statusBadge } from '../../config/pageTheme';
import { formatDateTimeIST } from '../../utils/formatDateTimeIST';
import { verificationInfo } from '../../utils/verificationStatus';
import { FormField } from '../../components/ui/FormField';
import { Modal } from '../../components/ui/Modal';
import { SettingsCard } from './SettingsCard';

function Skeleton({ label, height = 'h-24' }: { label: string; height?: string }) {
  return <div className={`${height} animate-pulse rounded-lg bg-soft`} role="status" aria-busy="true" aria-label={label} />;
}

function Retry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-err-b bg-err-bg px-4 py-3 text-sm text-err-fg" role="alert">
      <span>{message}</span>
      <button type="button" className={secondaryButton} onClick={onRetry}>Retry</button>
    </div>
  );
}

// ======================================================================= users and roles
export function UsersSection({ self }: { self: PortalUser }) {
  const toast = useToast();
  const [users, setUsers] = useState<PortalUser[] | null>(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [role, setRole] = useState<'all' | PortalUser['role']>('all');
  const [state, setState] = useState<'all' | 'active' | 'inactive'>('all');
  const [invite, setInvite] = useState({ name: '', email: '', role: 'msl' as PortalUser['role'] });
  const [inviteError, setInviteError] = useState('');
  const [busy, setBusy] = useState('');
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);
  const [deactivating, setDeactivating] = useState<PortalUser | null>(null);
  const [reason, setReason] = useState('');
  const [resetting, setResetting] = useState<PortalUser | null>(null);

  const load = useCallback(() => {
    setError('');
    listPortalUsers()
      .then(setUsers)
      .catch(() => setError('We could not load the users.'));
  }, []);
  useEffect(load, [load]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (users ?? []).filter((u) => {
      if (role !== 'all' && u.role !== role) return false;
      const active = (u.status || 'active') === 'active';
      if (state === 'active' && !active) return false;
      if (state === 'inactive' && active) return false;
      return !needle || `${u.display_name} ${u.email}`.toLowerCase().includes(needle);
    });
  }, [users, q, role, state]);

  const sendInvite = async () => {
    if (invite.name.trim().length < 2) return setInviteError('Enter the full name.');
    if (!/^\S+@\S+\.\S+$/.test(invite.email.trim())) return setInviteError('Enter a valid email address.');
    setInviteError('');
    setBusy('invite');
    try {
      const created = await createPortalUser({ email: invite.email.trim(), display_name: invite.name.trim(), role: invite.role });
      if (created.temporary_password) setSecret({ email: created.email, password: created.temporary_password });
      toast.success('Person saved.');
      setInvite({ name: '', email: '', role: invite.role });
      load();
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : 'Could not save this person.');
    } finally {
      setBusy('');
    }
  };

  // Resetting a password uses the same admin endpoint as inviting: it re-saves the user and returns a one-time password.
  const resetPassword = async () => {
    if (!resetting) return;
    setBusy(`reset-${resetting.user_id}`);
    try {
      const updated = await createPortalUser({ email: resetting.email, display_name: resetting.display_name, role: resetting.role });
      setResetting(null);
      if (updated.temporary_password) setSecret({ email: updated.email, password: updated.temporary_password });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not reset the password.');
    } finally {
      setBusy('');
    }
  };

  const setActive = async (u: PortalUser, active: boolean) => {
    setBusy(`state-${u.user_id}`);
    try {
      if (active) await reactivateDoctor(u.user_id, '');
      else await deactivateDoctor(u.user_id, reason.trim());
      setDeactivating(null);
      setReason('');
      load();
      toast.success(active ? 'Doctor reactivated.' : 'Doctor deactivated.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not change the account.');
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      <SettingsCard title="Invite a person" description="Creates the account and shows a one-time temporary password." testId="card-invite">
        <div className="settings-fields">
          <FormField id="invite-name" label="Full name" error={inviteError.startsWith('Enter the full') ? inviteError : ''}>
            <input id="invite-name" className={inputClass} value={invite.name} onChange={(e) => setInvite({ ...invite, name: e.target.value })} />
          </FormField>
          <FormField id="invite-email" label="Email" error={inviteError.startsWith('Enter a valid') ? inviteError : ''}>
            <input id="invite-email" type="email" className={inputClass} value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} />
          </FormField>
          <FormField id="invite-role" label="Role">
            <select id="invite-role" className={inputClass} value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value as PortalUser['role'] })}>
              <option value="msl">Physical MSL</option>
              <option value="admin">Administrator</option>
              <option value="doctor">Doctor</option>
            </select>
          </FormField>
        </div>
        {inviteError && !inviteError.startsWith('Enter') ? <p className="text-xs text-err-fg" role="alert">{inviteError}</p> : null}
        <button type="button" className={primaryButton} onClick={sendInvite} disabled={busy === 'invite'}>
          {busy === 'invite' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Save person
        </button>
      </SettingsCard>

      <SettingsCard
        title="Users and roles"
        description="Doctor verification decisions are made in Registered Doctors."
        testId="card-users"
        badge={<button type="button" className={secondaryButton} onClick={load}><RefreshCw className="h-4 w-4" aria-hidden="true" /> Refresh</button>}
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="relative min-w-[220px] flex-1">
            <label htmlFor="users-search" className="sr-only">Search users</label>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden="true" />
            <input id="users-search" className={`${inputClass} pl-9`} placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div>
            <label htmlFor="users-role" className="sr-only">Role</label>
            <select id="users-role" className={inputClass} value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
              <option value="all">All roles</option>
              <option value="doctor">Doctors</option>
              <option value="msl">Physical MSLs</option>
              <option value="admin">Administrators</option>
            </select>
          </div>
          <div>
            <label htmlFor="users-state" className="sr-only">Status</label>
            <select id="users-state" className={inputClass} value={state} onChange={(e) => setState(e.target.value as typeof state)}>
              <option value="all">Any status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </div>
        {error ? <Retry message={error} onRetry={load} /> : null}
        {users === null && !error ? <Skeleton label="Loading users" height="h-32" /> : null}
        {users && rows.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No users match.</p> : null}
        {rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
              <caption className="sr-only">Users</caption>
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted">
                  <th scope="col" className="bg-soft px-3 py-2.5">Name</th>
                  <th scope="col" className="bg-soft px-3 py-2.5">Role</th>
                  <th scope="col" className="bg-soft px-3 py-2.5">Status</th>
                  <th scope="col" className="bg-soft px-3 py-2.5">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => {
                  const active = (u.status || 'active') === 'active';
                  const info = u.role === 'doctor' && u.verification_status ? verificationInfo(u.verification_status) : null;
                  return (
                    <tr key={u.user_id} className="hover:bg-selbg">
                      <td className="border-b border-line px-3 py-2.5">
                        <p className="font-semibold text-fg">{u.display_name}</p>
                        <p className="break-all text-xs text-muted">{u.email}</p>
                      </td>
                      <td className="border-b border-line px-3 py-2.5"><span className={statusBadge('slate')}>{ROLE_LABEL[u.role]}</span></td>
                      <td className="border-b border-line px-3 py-2.5">
                        <div className="flex flex-wrap gap-1.5">
                          <span className={statusBadge(active ? 'blue' : 'amber')}>{active ? 'Active' : 'Inactive'}</span>
                          {info ? <span className={statusBadge(info.tone)}>{info.label}</span> : null}
                        </div>
                      </td>
                      <td className="border-b border-line px-3 py-2.5">
                        <div className="flex flex-wrap gap-2">
                          <button type="button" className={secondaryButton} onClick={() => setResetting(u)} disabled={u.user_id === self.user_id || busy !== ''} aria-label={`Reset password for ${u.display_name}`}>
                            Reset password
                          </button>
                          {u.role === 'doctor' ? (
                            active ? (
                              <button type="button" className={secondaryButton} onClick={() => setDeactivating(u)} disabled={busy !== ''} aria-label={`Deactivate ${u.display_name}`}>Deactivate</button>
                            ) : (
                              <button type="button" className={secondaryButton} onClick={() => setActive(u, true)} disabled={busy !== ''} aria-label={`Reactivate ${u.display_name}`}>Reactivate</button>
                            )
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="text-xs text-muted">Showing {rows.length} of {users?.length ?? 0}.</p>
      </SettingsCard>

      <Modal open={!!secret} onClose={() => setSecret(null)} title="Temporary password" testId="secret-modal">
        <p className="text-sm text-fg">Share this with {secret?.email} securely. It is shown once; they should change it after signing in.</p>
        <p className="mt-3 select-all rounded-lg bg-soft px-3 py-2 font-mono text-base text-fg">{secret?.password}</p>
        <div className="mt-4 flex justify-end"><button type="button" className={primaryButton} onClick={() => setSecret(null)}>Done</button></div>
      </Modal>
      <Modal open={!!resetting} onClose={() => setResetting(null)} title="Reset password?">
        <p className="text-sm text-fg">A new temporary password will be created for {resetting?.display_name}. Their current password stops working.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={() => setResetting(null)}>Cancel</button>
          <button type="button" className={primaryButton} onClick={resetPassword} disabled={busy !== ''}>Reset password</button>
        </div>
      </Modal>
      <Modal open={!!deactivating} onClose={() => setDeactivating(null)} title="Deactivate this doctor?">
        <p className="text-sm text-fg">{deactivating?.display_name} will be signed out and cannot use Digital MSL until reactivated.</p>
        <div className="mt-3">
          <FormField id="deactivate-reason" label="Reason" error={reason.trim().length < 3 ? 'Add a short reason (at least 3 characters).' : ''}>
            <input id="deactivate-reason" className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} />
          </FormField>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={() => setDeactivating(null)}>Cancel</button>
          <button type="button" className={primaryButton} disabled={reason.trim().length < 3 || busy !== ''} onClick={() => deactivating && setActive(deactivating, false)}>Deactivate</button>
        </div>
      </Modal>
    </>
  );
}

// ======================================================================= repositories and IKM
function maskUrl(raw: string): string {
  try {
    const u = new URL(raw);
    const host = /^\d+\.\d+\.\d+\.\d+$/.test(u.hostname)
      ? u.hostname.split('.').map((p, i) => (i < 2 ? p : '***')).join('.')
      : u.hostname.replace(/^([^.]{2})[^.]*/, '$1***');
    return `${u.protocol}//${host}${u.port ? `:${u.port}` : ''}`;
  } catch {
    return 'Not configured';
  }
}

const IKM_CODES: Record<string, string> = {
  OK: 'Reachable',
  TLS_ERROR: 'The secure connection (TLS) to IKM failed',
  TIMEOUT: 'IKM did not answer in time',
  CONNECTION_ERROR: 'IKM could not be reached',
  NOT_CONFIGURED: 'No IKM address is configured',
};

export function IkmSection() {
  const [ops, setOps] = useState<OpsStatus | null>(null);
  const [code, setCode] = useState('');
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    setError('');
    try {
      const health = await fetch(`${API_BASE_URL}/health`).then((r) => r.json()).catch(() => null);
      setCode(health?.ikm?.code ?? '');
      setOps(await getOpsStatus());
    } catch {
      setOps(null);
      setError('We could not read the IKM status.');
    } finally {
      setChecking(false);
      setCheckedAt(new Date());
    }
  }, []);
  useEffect(() => {
    void check();
  }, [check]);

  const reachable = !!ops?.ikm.reachable;
  const reason = IKM_CODES[code] ?? (reachable ? 'Reachable' : 'IKM is not reachable');

  return (
    <SettingsCard
      title="Repositories and IKM"
      description="Connection to the document platform that holds the approved repositories. Credentials are never shown."
      testId="card-ikm"
      badge={ops || error ? <span className={statusBadge(reachable ? 'blue' : 'amber')}>{reachable ? 'Connected' : 'Not connected'}</span> : null}
      footer={
        <button type="button" className={secondaryButton} onClick={() => void check()} disabled={checking} data-testid="ikm-test">
          {checking ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />} Test connection
        </button>
      }
    >
      {checking && !ops ? <Skeleton label="Checking IKM" /> : null}
      {error ? <Retry message={error} onRetry={() => void check()} /> : null}
      {ops ? (
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Status</dt><dd className="text-fg">{reason}</dd></div>
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Address</dt><dd className="text-fg">{maskUrl(ops.ikm.base_url)}</dd></div>
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Repositories</dt><dd className="text-fg">{reachable ? ops.ikm.repository_count : 'Not available'}</dd></div>
          <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Response time</dt><dd className="text-fg">{reachable && ops.ikm.latency_ms != null ? `${ops.ikm.latency_ms} ms` : 'Not available'}</dd></div>
        </dl>
      ) : null}
      {checkedAt ? <p className="text-xs text-muted">Checked {checkedAt.toLocaleTimeString()}.</p> : null}
    </SettingsCard>
  );
}

// ======================================================================= compliance notice
export function NoticeSection() {
  const [notice, setNotice] = useState<ComplianceNotice | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setError('');
    fetchComplianceNotice().then(setNotice).catch(() => setError('We could not load the compliance notice.'));
  }, []);
  useEffect(load, [load]);
  return (
    <SettingsCard
      title="Compliance notice"
      description="The notice doctors see on first contact and in every email. Medical, legal and regulatory approval is recorded outside this screen."
      testId="card-notice"
      badge={notice ? <span className={statusBadge('blue')}>Active · version {notice.version}</span> : null}
    >
      {error ? <Retry message={error} onRetry={load} /> : null}
      {!notice && !error ? <Skeleton label="Loading notice" /> : null}
      {notice ? <p className="whitespace-pre-wrap rounded-lg bg-soft px-4 py-3 text-sm text-fg">{notice.text}</p> : null}
    </SettingsCard>
  );
}

// ======================================================================= integrations
export function IntegrationsSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setError('');
    getDmslConfig().then((c) => setCfg(c as unknown as Record<string, unknown>)).catch(() => setError('We could not load the integration status.'));
  }, []);
  useEffect(load, [load]);
  const row = (name: string, on: boolean, hint: string) => (
    <li key={name} className="flex items-center justify-between gap-3 py-3">
      <div>
        <p className="text-sm font-semibold text-fg">{name}</p>
        <p className="text-xs text-muted">{hint}</p>
      </div>
      <span className={statusBadge(on ? 'blue' : 'amber')}>{on ? 'Configured' : 'Not configured'}</span>
    </li>
  );
  return (
    <SettingsCard title="Integrations" description="Whether each connection has its credentials set. Secrets are never displayed." testId="card-integrations">
      {error ? <Retry message={error} onRetry={load} /> : null}
      {!cfg && !error ? <Skeleton label="Loading integrations" /> : null}
      {cfg ? (
        <ul className="divide-y divide-line">
          {row('WhatsApp', !!cfg.whatsapp_configured, 'Credentials present: ' + (cfg.whatsapp_configured ? 'yes' : 'no'))}
          {row('AI model', !!cfg.llm_configured, 'Credentials present: ' + (cfg.llm_configured ? 'yes' : 'no'))}
          {row('IKM address', !!cfg.ikm_base_url, 'Address set: ' + (cfg.ikm_base_url ? 'yes' : 'no'))}
        </ul>
      ) : null}
    </SettingsCard>
  );
}

// ======================================================================= audit log
export function AuditSection() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [action, setAction] = useState('');
  const [user, setUser] = useState('');
  const [limit, setLimit] = useState(50);

  const load = useCallback(() => {
    setError('');
    setEvents(null);
    listAuditEvents({ action: action.trim() || undefined, limit })
      .then((r) => {
        setEvents(r.events);
        setTotal(r.total);
      })
      .catch(() => setError('We could not load the audit log.'));
  }, [action, limit]);
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const rows = (events ?? []).filter((e) => !user.trim() || e.user_id.toLowerCase().includes(user.trim().toLowerCase()));
  return (
    <SettingsCard title="Audit log" description="Append-only record of sign-ins, consent, verification, exports and configuration changes." testId="card-audit">
      <div className="flex flex-wrap items-end gap-3">
        <FormField id="audit-action" label="Action">
          <input id="audit-action" className={inputClass} placeholder="for example login" value={action} onChange={(e) => setAction(e.target.value)} />
        </FormField>
        <FormField id="audit-user" label="User id">
          <input id="audit-user" className={inputClass} value={user} onChange={(e) => setUser(e.target.value)} />
        </FormField>
        <FormField id="audit-limit" label="Show">
          <select id="audit-limit" className={inputClass} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[50, 100, 200].map((n) => <option key={n} value={n}>{n} latest</option>)}
          </select>
        </FormField>
      </div>
      {error ? <Retry message={error} onRetry={load} /> : null}
      {events === null && !error ? <Skeleton label="Loading audit log" height="h-32" /> : null}
      {events && rows.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No events match.</p> : null}
      {rows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
            <caption className="sr-only">Audit events</caption>
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                {['When', 'User', 'Role', 'Action', 'Outcome'].map((h) => <th key={h} scope="col" className="bg-soft px-3 py-2.5">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.event_id} className="hover:bg-selbg">
                  <td className="whitespace-nowrap border-b border-line px-3 py-2">{e.created_at ? formatDateTimeIST(e.created_at) : ''}</td>
                  <td className="border-b border-line px-3 py-2 font-mono text-xs">{e.user_id || '—'}</td>
                  <td className="border-b border-line px-3 py-2">{e.role || '—'}</td>
                  <td className="border-b border-line px-3 py-2">{e.action}</td>
                  <td className="border-b border-line px-3 py-2"><span className={statusBadge(e.outcome && !['ok', 'verified', 'granted'].includes(e.outcome) ? 'amber' : 'slate')}>{e.outcome || 'ok'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="text-xs text-muted">Showing {rows.length} of {total} events.</p>
    </SettingsCard>
  );
}

// ======================================================================= environment (read only)
export function EnvironmentSection() {
  const [cfg, setCfg] = useState<Awaited<ReturnType<typeof getDmslConfig>> | null>(null);
  const [error, setError] = useState('');
  const uiDemo = import.meta.env.VITE_DEMO_MODE === 'true';
  const load = useCallback(() => {
    setError('');
    getDmslConfig().then(setCfg).catch(() => setError('We could not load the environment.'));
  }, []);
  useEffect(load, [load]);
  return (
    <>
      {uiDemo ? (
        <div className="flex items-start gap-3 rounded-[14px] border border-err-b bg-err-bg px-5 py-4 text-sm text-err-fg" role="alert" data-testid="demo-banner">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Demo mode is on in this web app</p>
            <p>Sign-in fields are pre-filled with sample credentials. This must never be on in production.</p>
          </div>
        </div>
      ) : null}
      <SettingsCard title="Environment" description="Read-only settings in effect." testId="card-environment">
        {error ? <Retry message={error} onRetry={load} /> : null}
        {!cfg && !error ? <Skeleton label="Loading environment" /> : null}
        {cfg ? (
          <dl className="grid gap-4 text-sm sm:grid-cols-2">
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Web app demo mode</dt><dd><span className={statusBadge(uiDemo ? 'rose' : 'slate')}>{uiDemo ? 'On' : 'Off'}</span></dd></div>
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">AI model</dt><dd><span className={statusBadge(cfg.llm_configured ? 'blue' : 'amber')}>{cfg.llm_configured ? 'Configured' : 'Not configured'}</span></dd></div>
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Confidence threshold</dt><dd className="text-fg">{Math.round(cfg.confidence_threshold * 100)}%</dd></div>
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Minimum claim score</dt><dd className="text-fg">{cfg.claim_min_score != null ? `${Math.round(cfg.claim_min_score * 100)}%` : 'Not available'}</dd></div>
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">Channels</dt><dd className="text-fg">{(cfg.channels ?? []).join(', ') || 'Not available'}</dd></div>
            <div><dt className="text-xs font-semibold uppercase tracking-wide text-muted">In-scope medicines</dt><dd className="text-fg">{cfg.molecules.length}</dd></div>
          </dl>
        ) : null}
      </SettingsCard>
    </>
  );
}
