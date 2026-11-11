import type { ReactNode } from 'react';
import { Menu, Moon, Sun } from 'lucide-react';

/** 68px header: page title on the left; bell and theme toggle on the right. No logo (it lives in the sidebar). */
export function Header({
  title,
  theme,
  onToggleTheme,
  onOpenMenu,
  menuOpen,
  children,
}: {
  title: string;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  onOpenMenu: () => void;
  menuOpen: boolean;
  /** right-hand extras (notification bell) */
  children?: ReactNode;
}) {
  return (
    <header className="flex h-[68px] shrink-0 items-center justify-between gap-3 border-b border-line bg-card px-4 shell:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] border border-line bg-soft text-fg shell:hidden"
          onClick={onOpenMenu}
          aria-label="Open menu"
          aria-controls="app-sidebar"
          aria-expanded={menuOpen}
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>
        <h1 className="truncate text-xl font-semibold text-fg" data-testid="page-title">
          {title}
        </h1>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {children}
        <button
          type="button"
          onClick={onToggleTheme}
          className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-line bg-soft text-fg"
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          data-testid="theme-toggle"
        >
          {theme === 'dark' ? <Sun className="h-5 w-5" aria-hidden="true" /> : <Moon className="h-5 w-5" aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
}
