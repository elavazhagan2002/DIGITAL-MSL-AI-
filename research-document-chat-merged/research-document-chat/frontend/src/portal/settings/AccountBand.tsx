import { useEffect, useState } from 'react';
import { FileText, KeyRound } from 'lucide-react';
import { fetchComplianceNotice, type ComplianceNotice, type PortalUser } from '../../services/digitalMslService';
import { ROLE_LABEL } from '../AuthContext';
import { statusBadge } from '../../config/pageTheme';
import { verificationInfo } from '../../utils/verificationStatus';
import { Modal } from '../../components/ui/Modal';
import type { SectionId } from './sections';

function initials(name: string): string {
  const parts = name.replace(/^dr\.?\s+/i, '').split(/[\s,]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '?') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

/**
 * One full-width card: who is signed in, account status, two-step status and the compliance notice. It replaces the
 * old right-hand column, so nothing is empty at any width; it wraps to two rows under 960px.
 */
export function AccountBand({ user, onNavigate }: { user: PortalUser; onNavigate: (id: SectionId) => void }) {
  const [notice, setNotice] = useState<ComplianceNotice | null>(null);
  const [noticeOpen, setNoticeOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchComplianceNotice()
      .then((n) => !cancelled && setNotice(n))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const canMfa = user.role === 'msl' || user.role === 'admin';
  const status =
    user.role === 'doctor' && user.verification_status
      ? verificationInfo(user.verification_status)
      : { label: (user.status || 'Active').replace(/^./, (c) => c.toUpperCase()), tone: 'blue' as const };

  return (
    <section className="rounded-[14px] border border-line bg-card px-5 py-4" aria-label="Account" data-testid="settings-account-band">
      <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent text-base font-semibold text-accentfg" aria-hidden="true">
            {initials(user.display_name)}
          </span>
          <div className="min-w-0">
            <p className="truncate font-semibold text-fg">{user.display_name}</p>
            <p className="text-sm text-muted">
              {ROLE_LABEL[user.role]} <span aria-hidden="true">·</span> <span className="break-all">{user.email}</span>
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <span className={statusBadge(status.tone)} data-testid="band-status">{status.label}</span>
          {canMfa ? (
            <span className="flex items-center gap-2 text-sm text-fg" data-testid="band-two-step">
              <KeyRound className="h-4 w-4 text-muted" aria-hidden="true" />
              Two-step: <strong>{user.mfa_enabled ? 'On' : 'Off'}</strong>
              {!user.mfa_enabled ? (
                <button type="button" className="min-h-11 px-1 text-sm font-semibold text-link underline-offset-2 hover:underline" onClick={() => onNavigate('security')} data-testid="band-enable-mfa">
                  Enable
                </button>
              ) : null}
            </span>
          ) : null}
          <button
            type="button"
            className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-link underline-offset-2 hover:underline disabled:opacity-60"
            onClick={() => setNoticeOpen(true)}
            disabled={!notice}
            data-testid="band-notice"
          >
            <FileText className="h-4 w-4" aria-hidden="true" />
            Notice{notice ? ` version ${notice.version}` : ''}
          </button>
        </div>
      </div>
      <Modal open={noticeOpen} onClose={() => setNoticeOpen(false)} title="Compliance notice" testId="notice-modal">
        <p className="text-sm text-fg">{notice?.text}</p>
        {notice ? <p className="mt-3 text-xs text-muted">Notice version {notice.version}</p> : null}
      </Modal>
    </section>
  );
}
