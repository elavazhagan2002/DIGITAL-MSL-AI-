import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell, X } from 'lucide-react';
import { listCases, listNotifications, markNotificationsRead, type PortalNotification } from '../services/digitalMslService';

interface NotificationBellProps {
  userId: string;
  role?: 'admin' | 'doctor' | 'msl';
  suppressAutoPopup?: boolean;
  onOpenCase?: (caseId: string) => void;
}

function seenKey(userId: string) {
  return `dmsl_ntf_seen_${userId}`;
}

function readSeen(userId: string): Set<string> {
  try {
    const raw = sessionStorage.getItem(seenKey(userId));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function writeSeen(userId: string, ids: Set<string>) {
  sessionStorage.setItem(seenKey(userId), JSON.stringify([...ids]));
}

export function NotificationBell({ userId, role, suppressAutoPopup = false, onOpenCase }: NotificationBellProps) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<PortalNotification[]>([]);
  const seenRef = useRef<Set<string>>(readSeen(userId));
  const panelRef = useRef<HTMLDivElement>(null);
  void suppressAutoPopup;

  const remember = (ids: string[]) => {
    ids.forEach((id) => seenRef.current.add(id));
    writeSeen(userId, seenRef.current);
  };

  const load = useCallback(async () => {
    let next: PortalNotification[] = [];
    try {
      next = await listNotifications(userId);
    } catch {
      next = [];
    }
    if (role === 'msl') {
      try {
        const open = (await listCases({ viewerMslId: userId, status: 'open' })).filter((c) => c.status === 'open');
        const have = new Set(next.map((n) => n.case_id).filter(Boolean));
        for (const c of open) {
          if (have.has(c.case_id)) continue;
          next.push({
            notification_id: `case:${c.case_id}`,
            kind: 'new_question',
            title: 'New doctor question',
            body: c.question || 'A doctor asked a question.',
            case_id: c.case_id,
            read: false,
          });
          have.add(c.case_id);
        }
      } catch {
        /* queue still shown from notifications */
      }
    }
    setItems(next);
  }, [userId, role]);

  useEffect(() => {
    seenRef.current = readSeen(userId);
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load, userId]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!panelRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const unread = items.filter((n) => !n.read).length;

  const openFrom = (n: PortalNotification) => {
    remember([n.notification_id]);
    setOpen(false);
    if (n.case_id) onOpenCase?.(n.case_id);
    else onOpenCase?.('');
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-11 w-11 items-center justify-center rounded-[10px] border border-line bg-soft text-fg"
        aria-label="Notifications"
      >
        <Bell className="w-5 h-5" aria-hidden="true" />
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[1.1rem] h-4 px-1 rounded-full bg-err-fg text-card text-[10px] flex items-center justify-center">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-auto bg-card border border-line rounded-xl shadow-lg z-50">
          <div className="px-3 py-2 border-b border-line flex justify-between items-center">
            <span className="text-xs font-semibold text-fg">Updates</span>
            <div className="flex items-center gap-2">
            <button
              type="button"
              className="text-[11px] text-link"
              onClick={async () => {
                try {
                  await markNotificationsRead(userId);
                } catch {
                  /* synthetic queue items */
                }
                await load();
              }}
            >
              Mark all read
            </button>
            <button type="button" className="p-1 rounded text-muted hover:bg-soft" aria-label="Close notifications" onClick={() => setOpen(false)}>
              <X className="w-3.5 h-3.5" />
            </button>
            </div>
          </div>
          {items.length === 0 && <p className="p-4 text-xs text-muted">No updates yet.</p>}
          {items.map((n) => (
            <button
              key={n.notification_id}
              type="button"
              className={`w-full text-left px-3 py-2.5 border-b border-line ${n.read ? '' : 'bg-selbg'}`}
              onClick={async () => {
                try {
                  if (!n.notification_id.startsWith('case:')) {
                    await markNotificationsRead(userId, n.notification_id);
                  }
                } catch {
                  /* ignore */
                }
                await load();
                openFrom(n);
              }}
            >
              <p className="text-xs font-semibold text-fg">{n.title}</p>
              <p className="text-[11px] text-muted line-clamp-2 mt-0.5">{n.body}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
