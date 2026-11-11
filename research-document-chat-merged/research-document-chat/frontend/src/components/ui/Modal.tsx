import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

/**
 * Centred modal dialog. Real overlay (fixed + z-index); Esc and the scrim close it; focus moves into the
 * dialog on open and returns to the element that opened it.
 */
export function Modal({
  open,
  onClose,
  title,
  children,
  testId = 'modal',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
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
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-scrim p-4" onClick={onClose} role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-card shadow-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-line bg-soft text-fg hover:bg-selbg"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
