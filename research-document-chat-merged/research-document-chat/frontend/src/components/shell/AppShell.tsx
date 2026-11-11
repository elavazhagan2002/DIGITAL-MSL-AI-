import type { ReactNode } from 'react';

/**
 * AppShell (docs/ui-reference/shell.html `.app`): CSS grid, columns 264px | 1fr from 960px up, one column below.
 * Sidebar in the first column, one main column (header / content / footer) in the second. No absolute
 * positioning, no negative margins; the open drawer is a real overlay (fixed + z-index) outside the grid flow.
 * The page itself never scrolls; the content area (or the chat thread inside it) does.
 */
export function AppShell({
  sidebar,
  scrim,
  children,
}: {
  sidebar: ReactNode;
  /** off-canvas drawer backdrop (a real overlay) */
  scrim?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid h-screen min-h-screen w-full grid-cols-[minmax(0,1fr)] overflow-hidden bg-page text-fg shell:grid-cols-[264px_minmax(0,1fr)]">
      {sidebar}
      {scrim}
      <div className="flex h-full min-h-0 min-w-0 flex-col">{children}</div>
    </div>
  );
}

/** Scrolling content region. `fill` hands the full area to a single child (the chat) that scrolls inside itself. */
export function Content({ fill = false, children }: { fill?: boolean; children: ReactNode }) {
  return (
    <main
      className={`min-h-0 flex-1 ${fill ? 'flex overflow-hidden p-4 shell:px-6 shell:py-5' : 'overflow-y-auto p-4 shell:px-6 shell:py-5'}`}
      id="main-content"
    >
      {fill ? <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div> : children}
    </main>
  );
}

export function Footer() {
  return (
    <footer className="shrink-0 border-t border-line bg-card px-6 py-3.5 text-center text-[13px] text-muted">
      Digital MSL · Research Document Chat
    </footer>
  );
}
