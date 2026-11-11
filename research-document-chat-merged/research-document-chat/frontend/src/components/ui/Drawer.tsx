import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

/**
 * Right-hand drawer (kit Drawer). A real overlay: fixed + z-index. Esc and the scrim close it; focus moves
 * into the drawer on open and returns to whatever opened it.
 */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  badges,
  children,
  testId = 'drawer',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  badges?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      opener?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  // Portal to <body>: an ancestor with a transform or filter would otherwise capture position:fixed.
  return createPortal(
    <div className="fixed inset-0 z-[80] flex justify-end bg-scrim" onClick={onClose} role="presentation">
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        className="flex h-full w-full max-w-lg flex-col border-l border-line bg-card shadow-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {subtitle ? <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-link">{subtitle}</p> : null}
            <h2 className="mt-0.5 truncate text-lg font-semibold text-fg">{title}</h2>
            {badges ? <div className="mt-2 flex flex-wrap gap-1.5">{badges}</div> : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] border border-line bg-soft text-fg hover:bg-selbg"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}
