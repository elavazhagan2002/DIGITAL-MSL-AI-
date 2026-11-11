import { useEffect, useState } from 'react';
import { Loader2, LogOut, Monitor } from 'lucide-react';
import { listAuditEvents, type AuditEvent, type PortalUser } from '../../services/digitalMslService';
import { formatDateTimeIST } from '../../utils/formatDateTimeIST';
import { useAuth } from '../AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { secondaryButton, statusBadge } from '../../config/pageTheme';
import { Modal } from '../../components/ui/Modal';
import { SettingsCard } from './SettingsCard';

function describeDevice(ua: string): string {
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Mac OS/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : 'this device';
  return `${browser} on ${os}`;
}

export function ActivitySection({ user }: { user: PortalUser }) {
  const { logoutEverywhere } = useAuth();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [signIns, setSignIns] = useState<AuditEvent[] | null>(null);

  // The audit log is an administrator endpoint, so only administrators see their own recent sign-ins.
  useEffect(() => {
    if (user.role !== 'admin') return;
    listAuditEvents({ action: 'login', limit: 200 })
      .then((r) => setSignIns(r.events.filter((e) => e.user_id === user.user_id).slice(0, 8)))
      .catch(() => setSignIns([]));
  }, [user.role, user.user_id]);

  const signOutEverywhere = async () => {
    setLeaving(true);
    try {
      await logoutEverywhere();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not sign out of all devices.');
      setLeaving(false);
      setConfirm(false);
    }
  };

  return (
    <>
      <SettingsCard title="This device" description="The session you are using right now." icon={<Monitor className="h-4 w-4" aria-hidden="true" />} testId="card-this-device" badge={<span className={statusBadge('blue')}>Current</span>}>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Signed in as</dt>
            <dd className="text-fg">{user.display_name}</dd>
            <dd className="break-all text-muted">{user.email}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Device</dt>
            <dd className="text-fg">{describeDevice(typeof navigator !== 'undefined' ? navigator.userAgent : '')}</dd>
          </div>
        </dl>
      </SettingsCard>
      {user.role === 'admin' ? (
        <SettingsCard title="Recent sign-ins" description="Your latest sign-ins from the audit log." testId="card-recent-signins">
          {signIns === null ? <div className="h-16 animate-pulse rounded-lg bg-soft" role="status" aria-busy="true" aria-label="Loading sign-ins" /> : null}
          {signIns && signIns.length === 0 ? <p className="text-sm text-muted">No sign-ins recorded yet.</p> : null}
          {signIns && signIns.length > 0 ? (
            <ul className="divide-y divide-line text-sm">
              {signIns.map((e) => (
                <li key={e.event_id} className="flex justify-between gap-3 py-2">
                  <span className="text-fg">{e.created_at ? formatDateTimeIST(e.created_at) : 'Unknown time'}</span>
                  <span className="text-muted">{e.outcome || 'ok'}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </SettingsCard>
      ) : null}
      <SettingsCard
        title="Sign out everywhere"
        description="If you signed in on a shared or lost device, end every session. You will need to sign in again everywhere."
        testId="card-sign-out-all"
        footer={
          <button type="button" className={secondaryButton} onClick={() => setConfirm(true)} disabled={leaving} data-testid="sign-out-all">
            <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out of all devices
          </button>
        }
      />
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Sign out of every device?">
        <p className="text-sm text-fg">This ends your session here and on every other device.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={() => setConfirm(false)}>Cancel</button>
          <button type="button" className={secondaryButton} onClick={signOutEverywhere} disabled={leaving}>
            {leaving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Sign out everywhere
          </button>
        </div>
      </Modal>
    </>
  );
}
