import type { ReactNode } from 'react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import type { FriendlyError } from '../lib/friendlyError';
import { primaryButton, secondaryButton } from '../config/pageTheme';

/** Shared empty / error / loading states: icon, one-line title, one-line explanation, one primary action. */
export function StateCard({
  icon,
  title,
  text,
  action,
  tone = 'neutral',
  testId,
}: {
  icon: ReactNode;
  title: string;
  text?: string;
  action?: ReactNode;
  tone?: 'neutral' | 'error';
  testId?: string;
}) {
  return (
    <div
      className="mx-auto mt-10 max-w-lg rounded-2xl border border-line bg-card p-8 text-center shadow-card"
      role={tone === 'error' ? 'alert' : 'status'}
      data-testid={testId}
    >
      <div
        className={`mx-auto flex h-12 w-12 items-center justify-center rounded-full ${tone === 'error' ? 'bg-err-bg text-err-fg' : 'bg-selbg text-ok-fg'}`}
        aria-hidden="true"
      >
        {icon}
      </div>
      <h2 className="mt-4 text-base font-semibold text-fg">{title}</h2>
      {text ? <p className="mt-1 text-sm text-muted">{text}</p> : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}

/** One calm message, a Retry button, and the request id in small text. Never raw upstream text. */
export function ErrorCard({
  error,
  onRetry,
  retrying = false,
  title,
}: {
  error: FriendlyError;
  onRetry?: () => void;
  retrying?: boolean;
  title?: string;
}) {
  return (
    <StateCard
      tone="error"
      testId="error-card"
      icon={<AlertCircle className="h-6 w-6" />}
      title={title ?? error.message}
      text={title ? error.message : undefined}
      action={
        <div className="flex flex-col items-center gap-2">
          {onRetry ? (
            <button type="button" className={primaryButton} onClick={onRetry} disabled={retrying}>
              {retrying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
              Retry
            </button>
          ) : null}
          {error.requestId ? <p className="text-xs text-muted">Reference: {error.requestId}</p> : null}
        </div>
      }
    />
  );
}

export function LoadingCard({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="mx-auto mt-10 flex max-w-lg items-center justify-center gap-2 rounded-2xl border border-line bg-card p-8 text-sm text-muted" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {label}
    </div>
  );
}

export { secondaryButton };
