import type { ReactNode } from 'react';

/** Card used by every settings section: title, one-line description, content, optional footer with the primary action. */
export function SettingsCard({
  title,
  description,
  icon,
  badge,
  footer,
  children,
  testId,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  badge?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="rounded-[14px] border border-line bg-card" data-testid={testId} aria-label={title}>
      <div className="flex items-start justify-between gap-3 px-5 pb-0 pt-5">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-base font-semibold text-fg">
            {icon ? <span className="text-link">{icon}</span> : null}
            {title}
          </h2>
          {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
        </div>
        {badge}
      </div>
      {children ? <div className="space-y-4 px-5 py-4">{children}</div> : <div className="h-4" />}
      {footer ? <div className="flex flex-wrap items-center gap-3 border-t border-line px-5 py-3">{footer}</div> : null}
    </section>
  );
}
