import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import {
  Activity,
  Bell,
  ClipboardList,
  FileCheck2,
  FileText,
  Layers,
  Lock,
  MessageSquareText,
  Plug,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Stethoscope,
  User,
  Users,
  Database,
  type LucideIcon,
} from 'lucide-react';
import type { SectionDef, SectionId } from './sections';

const ICONS: Record<SectionId, LucideIcon> = {
  profile: User,
  security: Lock,
  notifications: Bell,
  preferences: SlidersHorizontal,
  activity: Activity,
  wording: MessageSquareText,
  myareas: Stethoscope,
  areas: Stethoscope,
  consent: ShieldCheck,
  registration: FileCheck2,
  users: Users,
  adminareas: Layers,
  ikm: Database,
  notice: FileText,
  scope: SlidersHorizontal,
  integrations: Plug,
  audit: ClipboardList,
  environment: Server,
};

/**
 * Settings page. It uses the whole main column (no centred container): page header, then a full-width account
 * band, then [sub-navigation 240px | content]. Content cards stack vertically (max 880px, or the full column for
 * management tables). Under 960px the sub-navigation is a horizontally scrollable tab row above the content.
 */
export function SettingsLayout({
  title,
  description,
  band,
  banner,
  sections,
  activeId,
  onSelect,
  children,
}: {
  title: string;
  description: string;
  band: ReactNode;
  banner?: ReactNode;
  sections: SectionDef[];
  activeId: SectionId;
  onSelect: (id: SectionId) => void;
  children: ReactNode;
}) {
  const navRef = useRef<HTMLElement>(null);
  const active = sections.find((s) => s.id === activeId) ?? sections[0];

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    const items = Array.from(navRef.current?.querySelectorAll<HTMLButtonElement>('button[data-section]') ?? []);
    const index = items.findIndex((el) => el === document.activeElement);
    if (index < 0) return;
    e.preventDefault();
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? items.length - 1
          : (index + (e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  };

  return (
    <div className="settings-root" data-testid="settings-root">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold text-fg">{title}</h1>
        <p className="mt-1 text-sm text-muted">{description}</p>
      </div>
      {banner}
      <div className="mb-6">{band}</div>
      <div className="settings-grid">
        <nav ref={navRef} className="settings-nav" aria-label="Settings sections" onKeyDown={onKeyDown} data-testid="settings-nav">
          {sections.map((section) => {
            const Icon = ICONS[section.id];
            const isActive = section.id === active.id;
            return (
              <button
                key={section.id}
                type="button"
                data-section={section.id}
                data-testid={`settings-nav-${section.id}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelect(section.id)}
                className={`flex h-11 shrink-0 items-center gap-3 whitespace-nowrap rounded-[10px] px-3 text-left text-sm transition-colors ${
                  isActive
                    ? 'bg-selbg font-semibold text-fg shadow-[inset_3px_0_0_var(--selb)]'
                    : 'font-medium text-muted hover:bg-soft hover:text-fg'
                }`}
              >
                <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                {section.label}
              </button>
            );
          })}
        </nav>
        <div className={`settings-content space-y-4 ${active.wide ? 'wide' : ''}`} data-testid="settings-content">
          {children}
        </div>
      </div>
    </div>
  );
}
