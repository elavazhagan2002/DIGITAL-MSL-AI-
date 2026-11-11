import { Clock, FileWarning, Loader2, Mail, RefreshCw, ShieldX } from 'lucide-react';
import type { VerificationStatus } from '../services/digitalMslService';
import { secondaryButton } from '../config/pageTheme';

const COPY: Record<Exclude<VerificationStatus, 'VERIFIED'>, { title: string; body: string; icon: typeof Clock; ring: string }> = {
  PENDING_CONTACT_VERIFICATION: {
    title: 'Verify your email and phone',
    body: 'Enter the codes we sent to your email and mobile number to continue. Your registration is then passed to our team for verification.',
    icon: Mail,
    ring: 'bg-warn-bg text-warn-fg',
  },
  PENDING_REGISTRATION_VERIFICATION: {
    title: 'Your registration is pending verification',
    body: 'Your Physical MSL team is checking your registration details against the National Medical Register (NMR). Digital MSL unlocks as soon as they confirm. You will not need to register again.',
    icon: Clock,
    ring: 'bg-warn-bg text-warn-fg',
  },
  NEEDS_INFO: {
    title: 'We need a little more information',
    body: 'Your Physical MSL team could not complete verification with the details provided. Please review the note below and contact them, then check again.',
    icon: FileWarning,
    ring: 'bg-selbg text-ok-fg',
  },
  REJECTED: {
    title: 'Your registration could not be verified',
    body: 'We were unable to match your details to the National Medical Register. If you believe this is a mistake, please contact your Physical MSL team or administrator.',
    icon: ShieldX,
    ring: 'bg-err-bg text-err-fg',
  },
};

export function VerificationPending({
  status,
  notes,
  loading,
  onRefresh,
  onOpenSettings,
}: {
  status: VerificationStatus | undefined;
  notes?: string;
  loading: boolean;
  onRefresh: () => void;
  onOpenSettings: () => void;
}) {
  if (!status && loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted">
        <Loader2 className="w-4 h-4 animate-spin" /> Checking your account…
      </div>
    );
  }
  const key: Exclude<VerificationStatus, 'VERIFIED'> =
    status && status !== 'VERIFIED' && status in COPY ? (status as Exclude<VerificationStatus, 'VERIFIED'>) : 'PENDING_REGISTRATION_VERIFICATION';
  const copy = COPY[key];
  const Icon = copy.icon;
  return (
    <div className="max-w-xl mx-auto mt-12">
      <div className="rounded-2xl border border-line bg-card shadow-sm overflow-hidden">
        <div className="sciagen-hero px-6 py-5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-brandcyan">Account verification</p>
          <h2 className="mt-1 text-xl font-semibold text-onnavy">{copy.title}</h2>
        </div>
        <div className="p-6 space-y-4">
          <div className="flex items-start gap-3">
            <span className={`rounded-xl p-2.5 ${copy.ring}`}>
              <Icon className="w-5 h-5" />
            </span>
            <p className="text-sm text-muted leading-relaxed">{copy.body}</p>
          </div>
          {notes ? (
            <div className="rounded-lg border border-line bg-soft px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Note from the verification team</p>
              <p className="mt-1 text-sm text-fg whitespace-pre-wrap">{notes}</p>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondaryButton} onClick={onRefresh} disabled={loading}>
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Check again
            </button>
            <button type="button" className={secondaryButton} onClick={onOpenSettings}>
              View my registration
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
