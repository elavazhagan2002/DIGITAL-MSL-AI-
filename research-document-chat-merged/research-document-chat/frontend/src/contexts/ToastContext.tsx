import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle, X } from 'lucide-react';
import { friendlyError } from '../lib/friendlyError';

export type ToastType = 'success' | 'error' | 'warning';

export interface ToastItem {
  id: string;
  /** de-duplication key: the same type + message never appears twice at once */
  key: string;
  type: ToastType;
  message: string;
}

interface ToastContextType {
  success: (message: string) => void;
  error: (message: string) => void;
  warning: (message: string) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const TOAST_AUTO_DISMISS_MS = 6000;
export const TOAST_MAX_VISIBLE = 3;

function ToastIcon({ type }: { type: ToastType }) {
  const cls = 'h-5 w-5 flex-shrink-0';
  switch (type) {
    case 'success':
      return <CheckCircle className={`${cls} text-ok-fg`} aria-hidden="true" />;
    case 'warning':
      return <AlertTriangle className={`${cls} text-warn-fg`} aria-hidden="true" />;
    default:
      return <AlertCircle className={`${cls} text-err-fg`} aria-hidden="true" />;
  }
}

function toastStyles(type: ToastType): string {
  switch (type) {
    case 'success':
      return 'border-selb bg-card';
    case 'warning':
      return 'border-warn-line bg-warn-bg';
    default:
      return 'border-err-line bg-err-bg';
  }
}

/** Raw technical wording is replaced by calm text before it is ever shown. */
function userFacing(message: unknown): string {
  const text = typeof message === 'string' ? message : '';
  return friendlyError(text || undefined, 'Something went wrong. Please try again.').message;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, number>>(new Map());

  const clearTimer = useCallback((id: string) => {
    const handle = timers.current.get(id);
    if (handle !== undefined) window.clearTimeout(handle);
    timers.current.delete(id);
  }, []);

  const dismiss = useCallback(
    (id: string) => {
      clearTimer(id);
      setToasts((prev) => prev.filter((t) => t.id !== id));
    },
    [clearTimer],
  );

  const startTimer = useCallback(
    (id: string) => {
      clearTimer(id);
      timers.current.set(id, window.setTimeout(() => dismiss(id), TOAST_AUTO_DISMISS_MS));
    },
    [clearTimer, dismiss],
  );

  const push = useCallback(
    (type: ToastType, rawMessage: string) => {
      const message = userFacing(rawMessage);
      const key = `${type}:${message}`;
      let existingId = '';
      setToasts((prev) => {
        const hit = prev.find((t) => t.key === key);
        if (hit) {
          existingId = hit.id;
          return prev; // same message: keep one, refresh its timer below
        }
        const id = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        existingId = id;
        const next = [...prev, { id, key, type, message }];
        const overflow = next.length - TOAST_MAX_VISIBLE;
        if (overflow > 0) next.slice(0, overflow).forEach((t) => clearTimer(t.id));
        return overflow > 0 ? next.slice(overflow) : next;
      });
      if (existingId) startTimer(existingId);
    },
    [clearTimer, startTimer],
  );

  useEffect(() => {
    const handles = timers.current;
    return () => handles.forEach((h) => window.clearTimeout(h));
  }, []);

  const success = useCallback((message: string) => push('success', message), [push]);
  const error = useCallback((message: string) => push('error', message), [push]);
  const warning = useCallback((message: string) => push('warning', message), [push]);

  // Stable identity: callers list `toast` in effect dependencies.
  const value = useMemo<ToastContextType>(() => ({ success, error, warning }), [success, error, warning]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[200] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
        aria-live="polite"
        aria-relevant="additions"
        data-testid="toast-region"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 shadow-card animate-fade-in ${toastStyles(toast.type)}`}
            role={toast.type === 'error' ? 'alert' : 'status'}
            data-testid="toast"
            onMouseEnter={() => clearTimer(toast.id)}
            onMouseLeave={() => startTimer(toast.id)}
            onFocus={() => clearTimer(toast.id)}
            onBlur={() => startTimer(toast.id)}
          >
            <ToastIcon type={toast.type} />
            <p className={`flex-1 pt-0.5 text-sm font-medium ${toast.type === 'error' ? 'text-err-fg' : toast.type === 'warning' ? 'text-warn-fg' : 'text-fg'}`}>
              {toast.message}
            </p>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              className="-my-1.5 -mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-soft hover:text-fg"
              aria-label="Dismiss notification"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
