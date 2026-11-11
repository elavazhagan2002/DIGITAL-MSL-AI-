/**
 * Shared workspace chrome (UI standards — stable colors, compact layout).
 * Sidebar styling lives in Layout.tsx and is unchanged.
 */

/** Workspace root: fills main scroll area with compact horizontal padding */
export const workspaceRoot = 'h-full px-2 py-2 sm:px-3';

/** Legacy alias */
export const pageRoot = workspaceRoot;

/** Content column — full width, tight vertical rhythm (no max-w-7xl cap) */
export const workspaceInner = 'w-full space-y-3';

/** Legacy alias */
export const pageInner = workspaceInner;

/** Optional alias used by repository and dense data screens */
export const compactWorkspaceShell = workspaceRoot;
export const compactWorkspaceContent = workspaceInner;

/** Toolbar row: subtitle + actions on one line when space allows */
export const toolbarRow = 'flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between';

export const pageSubtitle = 'text-sm text-muted';

/** Header shows page title — keep workspace headings sr-only when needed */
export const pageTitle = 'sr-only';

/** Primary CTA — solid accent, no gradients */
export const primaryButton =
  'inline-flex items-center justify-center gap-2 rounded-[10px] bg-accent px-5 py-2 text-sm font-semibold text-accentfg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';

export const secondaryButton =
  'inline-flex items-center justify-center gap-2 rounded-[10px] border-[1.5px] border-line bg-card px-4 py-2 text-sm font-semibold text-fg transition-colors hover:bg-soft disabled:opacity-50';

/** Veeva Vault brand orange — use for Vault navigation and upload actions */
export const veevaButton =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accentfg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';

export const veevaButtonCompact =
  'inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accentfg shadow-sm transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60';

export const dangerButton =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-err-fg px-4 py-2 text-sm font-semibold text-card transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';

/** Card / panel on a page */
export const cardSurface =
  'rounded-lg border border-line bg-card';

/** Compact stat tile in a stats bar */
export const statTile =
  'flex min-w-0 flex-1 items-center gap-3 rounded-lg border border-line bg-card px-3 py-2';

export const statValue = 'text-lg font-bold tabular-nums text-fg';

export const statLabel = 'text-xs font-medium text-muted';

/** Inline stats row (repositories, documents, etc.) */
export const inlineStatsRow = 'flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted';

/** Icon tile in headers / stats */
export const iconTile = 'rounded-lg bg-selbg p-2';

export const iconTileIcon = 'h-5 w-5 text-link';

/** Table or list section header strip */
export const panelHeader =
  'border-b border-line bg-soft px-4 py-2.5';

/** Shared form controls */
export const inputClass =
  'w-full min-h-[42px] rounded-[10px] border-[1.5px] border-fieldb bg-field px-3 py-2 text-sm text-fg outline-none focus:border-selb focus:ring-2 focus:ring-selb disabled:opacity-60';

export const filterInputClass =
  'h-11 rounded-[10px] border-[1.5px] border-fieldb bg-field px-3 text-sm text-fg outline-none focus:border-selb focus:ring-2 focus:ring-selb';

export const labelClass = 'mb-1 block text-xs font-semibold text-muted';

/** Status pill. Tones: blue (positive), cyan (in progress), amber (attention), rose (problem), slate (neutral). */
export type BadgeTone = 'blue' | 'cyan' | 'amber' | 'rose' | 'slate';

export function statusBadge(tone: BadgeTone = 'slate'): string {
  const map: Record<BadgeTone, string> = {
    blue: 'border border-selb bg-selbg text-ok-fg',
    cyan: 'border border-selb bg-selbg text-ok-fg',
    amber: 'border border-warn-line bg-warn-bg text-warn-fg',
    rose: 'border border-err-line bg-err-bg text-err-fg',
    slate: 'border border-line bg-soft text-fg',
  };
  return `inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${map[tone]}`;
}
