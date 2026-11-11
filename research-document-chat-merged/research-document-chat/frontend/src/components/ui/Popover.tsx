import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * Small popover opened by a trigger button. The panel is position:fixed from the trigger's rectangle so a
 * scrolling table card cannot clip it. Esc, outside click, scroll and resize close it.
 */
export function Popover({
  trigger,
  ariaLabel,
  children,
  testId = 'popover',
  triggerClassName = '',
}: {
  trigger: ReactNode;
  ariaLabel: string;
  children: ReactNode;
  testId?: string;
  triggerClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panelRef.current?.contains(t) && !buttonRef.current?.contains(t)) setOpen(false);
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [open]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) setPos({ top: rect.bottom + 6, left: Math.max(8, Math.min(rect.left, window.innerWidth - 248)) });
    setOpen((o) => !o);
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {open &&
        createPortal(
        <div
          ref={panelRef}
          role="dialog"
          aria-label={ariaLabel}
          data-testid={testId}
          style={{ top: pos.top, left: pos.left }}
          className="fixed z-[70] w-60 rounded-xl border border-line bg-card p-3 shadow-card"
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </div>,
        document.body,
        )}
    </>
  );
}
