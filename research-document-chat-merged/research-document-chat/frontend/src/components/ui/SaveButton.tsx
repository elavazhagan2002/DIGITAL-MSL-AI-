import { Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * Primary save action. A normal primary button; when there is nothing to save it is a flat, readable grey button
 * (not a faded copy of the primary one), so the label always keeps its contrast.
 */
export function SaveButton({
  onClick,
  disabled,
  saving = false,
  testId,
  children = 'Save changes',
}: {
  onClick: () => void;
  disabled: boolean;
  saving?: boolean;
  testId?: string;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || saving}
      data-testid={testId}
      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] border-[1.5px] border-transparent bg-accent px-5 text-sm font-semibold text-accentfg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:border-line disabled:bg-soft disabled:text-muted disabled:hover:opacity-100"
    >
      {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}
