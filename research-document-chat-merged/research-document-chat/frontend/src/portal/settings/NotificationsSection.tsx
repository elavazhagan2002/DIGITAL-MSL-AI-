import { useCallback, useEffect, useState } from 'react';
import { Bell, CheckCheck, Loader2 } from 'lucide-react';
import { listNotifications, markNotificationsRead, type PortalNotification, type PortalUser } from '../../services/digitalMslService';
import { secondaryButton, statusBadge } from '../../config/pageTheme';
import { formatDateTimeIST } from '../../utils/formatDateTimeIST';
import { SettingsCard } from './SettingsCard';

export function NotificationsSection({ user }: { user: PortalUser }) {
  const [items, setItems] = useState<PortalNotification[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError('');
    listNotifications(user.user_id)
      .then((list) => setItems(list))
      .catch(() => setError('We could not load your notifications.'));
  }, [user.user_id]);
  useEffect(load, [load]);

  const unread = (items ?? []).filter((n) => !n.read).length;
  const markAll = async () => {
    setBusy(true);
    try {
      await markNotificationsRead(user.user_id);
      load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsCard
      title="Recent notifications"
      description="Updates about your cases, replies and registration. Messaging channels are managed under consent."
      icon={<Bell className="h-4 w-4" aria-hidden="true" />}
      testId="card-notifications"
      footer={
        <button type="button" className={secondaryButton} onClick={markAll} disabled={busy || unread === 0}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <CheckCheck className="h-4 w-4" aria-hidden="true" />} Mark all as read
        </button>
      }
    >
      {error ? <p className="text-sm text-err-fg" role="alert">{error}</p> : null}
      {items === null && !error ? (
        <div className="space-y-2" role="status" aria-busy="true" aria-label="Loading notifications">
          {[0, 1, 2].map((i) => <div key={i} className="h-12 animate-pulse rounded-lg bg-soft" />)}
        </div>
      ) : null}
      {items && items.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No notifications yet.</p> : null}
      {items && items.length > 0 ? (
        <ul className="divide-y divide-line">
          {items.slice(0, 20).map((n) => (
            <li key={n.notification_id} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-fg">{n.title}</p>
                {n.body ? <p className="line-clamp-2 text-sm text-muted">{n.body}</p> : null}
                {n.created_at ? <p className="mt-0.5 text-xs text-muted">{formatDateTimeIST(n.created_at)}</p> : null}
              </div>
              {!n.read ? <span className={statusBadge('cyan')}>New</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </SettingsCard>
  );
}
