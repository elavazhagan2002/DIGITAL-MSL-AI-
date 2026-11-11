import { secondaryButton } from '../../config/pageTheme';
import { SaveButton } from './SaveButton';

/**
 * Sticky save bar for a dirty form. Rendered only while there are unsaved changes; it sticks to the bottom of the
 * scrolling content area, so showing it never shifts the layout above it.
 */
export function SaveBar({
  dirty,
  saving,
  onSave,
  onDiscard,
  canSave = true,
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  canSave?: boolean;
}) {
  if (!dirty) return null;
  return (
    <div
      className="sticky bottom-0 z-10 -mx-1 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-line bg-card px-4 py-3 shadow-card"
      role="region"
      aria-label="Unsaved changes"
      data-testid="save-bar"
    >
      <p className="text-sm font-semibold text-fg">Unsaved changes</p>
      <div className="flex items-center gap-2">
        <button type="button" className={secondaryButton} onClick={onDiscard} disabled={saving} data-testid="save-bar-discard">
          Discard
        </button>
        <SaveButton onClick={onSave} disabled={!canSave} saving={saving} testId="save-bar-save">
          Save changes
        </SaveButton>
      </div>
    </div>
  );
}
