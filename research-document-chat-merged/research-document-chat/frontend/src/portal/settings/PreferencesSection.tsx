import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react';
import { useTheme, type ThemePreference } from '../../contexts/ThemeContext';
import { SettingsCard } from './SettingsCard';

const OPTIONS: Array<{ id: ThemePreference; label: string; hint: string; Icon: LucideIcon }> = [
  { id: 'light', label: 'Light', hint: 'Always light', Icon: Sun },
  { id: 'dark', label: 'Dark', hint: 'Always dark', Icon: Moon },
  { id: 'system', label: 'System', hint: 'Follow this device', Icon: Monitor },
];

export function PreferencesSection() {
  const { preference, setPreference } = useTheme();
  return (
    <SettingsCard title="Theme" description="Choose how Digital MSL looks on this device." testId="card-theme">
      <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-3">
        {OPTIONS.map(({ id, label, hint, Icon }) => {
          const on = preference === id;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={on}
              data-testid={`theme-${id}`}
              onClick={() => setPreference(id)}
              className={`flex min-h-[64px] items-center gap-3 rounded-xl border-2 px-3 text-left transition-colors ${
                on ? 'border-selb bg-selbg' : 'border-line bg-card hover:bg-soft'
              }`}
            >
              <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] ${on ? 'bg-accent text-accentfg' : 'bg-soft text-muted'}`}>
                <Icon className="h-5 w-5" aria-hidden="true" />
              </span>
              <span>
                <span className="block text-sm font-semibold text-fg">{label}</span>
                <span className="block text-xs text-muted">{hint}</span>
              </span>
            </button>
          );
        })}
      </div>
    </SettingsCard>
  );
}
