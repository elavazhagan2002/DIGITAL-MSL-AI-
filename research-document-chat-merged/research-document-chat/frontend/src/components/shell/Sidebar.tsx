import type { ReactNode } from 'react';
import { LogOut, X } from 'lucide-react';

export interface NavItem {
  id: string;
  label: string;
  icon: ReactNode;
}

/**
 * Sidebar (264px, solid navy). Contains ONLY: brand (DIGITAL MSL label), role label, main nav,
 * user name and Sign out. Below 960px it is an off-canvas drawer: hidden until the header's menu button
 * opens it (the close button exists only inside the open drawer).
 */
export function Sidebar({
  nav,
  activeId,
  onSelect,
  userName,
  roleLabel,
  userMeta,
  onSignOut,
  drawerOpen,
  onCloseDrawer,
}: {
  nav: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
  userName: string;
  roleLabel: string;
  /** second line under the user name, for example 'Doctor · Verified' */
  userMeta?: string;
  onSignOut: () => void;
  drawerOpen: boolean;
  onCloseDrawer: () => void;
}) {
  return (
    <aside
      id="app-sidebar"
      aria-label="Main navigation"
      className={`${
        drawerOpen ? 'fixed inset-y-0 left-0 z-50 flex shadow-card' : 'hidden'
      } h-full w-[264px] flex-col justify-between gap-6 overflow-y-auto border-r border-navyline bg-navy px-4 py-6 text-onnavytext shell:static shell:z-auto shell:flex shell:w-auto shell:shadow-none`}
    >
      <div className="min-h-0">
        <div className="flex items-start justify-between gap-2 px-2">
          <div className="flex flex-col gap-1">
            <h1 className="text-xl font-bold tracking-tight text-onnavy">Digital MSL</h1>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brandcyan">
              Therapeutic Area Science
            </p>
            <p className="text-[13px] text-onnavymuted" data-testid="sidebar-role">
              {roleLabel}
            </p>
          </div>
          {drawerOpen && (
            <button
              type="button"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[10px] text-onnavy hover:bg-navyhover shell:hidden"
              onClick={onCloseDrawer}
              aria-label="Close menu"
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          )}
        </div>

        <nav className="mt-7 flex flex-col gap-1" aria-label="Sections">
          {nav.map((item) => {
            const active = activeId === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                aria-current={active ? 'page' : undefined}
                data-active={active ? 'true' : 'false'}
                data-testid={`nav-${item.id}`}
                className={`flex h-11 w-full items-center gap-3 rounded-[10px] px-3 text-left text-[15px] transition-colors ${
                  active
                    ? 'bg-navyactive font-semibold text-onnavy shadow-[inset_3px_0_0_var(--brand-cyan)]'
                    : 'font-medium text-onnavytext hover:bg-navyhover'
                }`}
              >
                {item.icon}
                {item.label}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="flex shrink-0 flex-col gap-3 px-2">
        <div className="border-t border-navyoutline pt-4">
          <p className="truncate font-semibold text-onnavy" data-testid="sidebar-user">
            {userName}
          </p>
          <p className="text-[13px] text-onnavymuted" data-testid="sidebar-user-meta">
            {userMeta || roleLabel}
          </p>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          className="flex h-11 w-full items-center justify-center gap-2.5 rounded-[10px] border-[1.5px] border-navyoutline bg-transparent text-[15px] font-semibold text-onnavy transition-colors hover:bg-navyhover"
          data-testid="sign-out"
        >
          <LogOut className="h-5 w-5" aria-hidden="true" /> Sign out
        </button>
      </div>
    </aside>
  );
}
