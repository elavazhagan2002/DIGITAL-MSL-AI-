import { AlertCircle } from 'lucide-react';
import type { ReactNode } from 'react';

/** Label above the control, optional helper text, inline error with icon and text. */
export function FormField({
  id,
  label,
  helper,
  error,
  children,
}: {
  id: string;
  label: string;
  helper?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-semibold text-fg">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="flex items-center gap-1.5 text-xs text-err-fg" role="alert">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {error}
        </p>
      ) : helper ? (
        <p id={`${id}-help`} className="text-xs text-muted">
          {helper}
        </p>
      ) : null}
    </div>
  );
}
