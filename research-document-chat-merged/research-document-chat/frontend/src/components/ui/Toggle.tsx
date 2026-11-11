/** Switch (role="switch"). 44px-high hit area, token colours only. */
export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
  id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex h-11 w-14 shrink-0 items-center justify-center rounded-[10px] disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span className={`relative h-6 w-11 rounded-full border border-line transition-colors ${checked ? 'bg-accent' : 'bg-soft'}`}>
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full shadow-card transition-all ${
            checked ? 'left-[22px] bg-accentfg' : 'left-0.5 bg-fieldb'
          }`}
        />
      </span>
    </button>
  );
}
