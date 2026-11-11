# Fix D (Settings): defects and root causes

Measured on the current build at 1440x900 with a Playwright probe (demo accounts), before any change.

| Role | Main width | Widest card | Rightmost card edge | Empty part of the content area |
| --- | --- | --- | --- | --- |
| Physical MSL | 1176 px | 768 px | x = 1056 (content ends at 1416) | 360 px, about 32 percent (at 1920 wide: about 52 percent) |
| Administrator (People) | 1176 px | 1128 px | 1416 | none, but no structure: a bare list under pill buttons |
| Doctor | 1176 px | 552 px x 2 columns | 1416 | none, but the cards are in a flat grid, no navigation |

## 1. Why the right half is empty (Physical MSL, and Administrator forms)
`SettingsPage.tsx` renders each card with a hard `max-w-3xl` (768 px) in a single stacked column, directly inside the shell's full-width content area. There is no page container, no grid and no second column, so everything above 768 px is blank. The Doctor view used `lg:grid-cols-2` instead, so the three roles had three different layouts.

## 2. Why "Two-step verification" is cut off
The content area is a fixed-height scroll region (`Content`: `overflow-y: auto`, 784 px high at 900 px viewport height). The Physical MSL page is one tall column: Account (to y = 662), Two-step verification (to y = 828), Sessions (to y = 1008). At 1366x768 the visible bottom of the region is about y = 702, so the Two-step card starts above the fold and ends below it, with no sticky navigation and no cue that the region scrolls. The card is not clipped by `overflow: hidden`; it is below the fold in a long single column. A two-pane layout with the sections split into separate views removes the long column.

## 3. Why the tab pills look like buttons
`tabBtn()` renders each tab as a filled `bg-accent` rounded button (`px-3 py-2 rounded-lg`) in a wrapping flex row. There is no tablist semantics, no underline or left indicator, and Administrator and Physical MSL use different tab sets. The kit's selected style (tinted background with a left indicator) was not used.

## 4. Other defects found while reading the code
- Account and password forms had no inline validation, no confirm field, no strength rules and no dirty-state handling.
- The email field was read-only but had no lock icon or explanation.
- The Doctor page had no way to make an area default, and showed consent only as switches.
- Three roles, three layouts, no shared aside or help content.

## 5. "The app shell is missing on the Settings route" (reported on a 1606 px screenshot)

Not reproducible on the current build, for any role. Evidence:

- `frontend/src/App.tsx:345-396`: there is exactly one `<AppShell>` and every page, Settings included, is rendered inside its `<Content>` (`App.tsx:381`, `App.tsx:383` `{page === 'settings' && <SettingsPage />}`). There is no separate route or layout for Settings, and `SettingsPage` itself renders no shell and does not hide the sidebar.
- `frontend/e2e/settings.spec.ts` ("shell <role>: the Settings route is inside the app shell") signs in as Doctor, Physical MSL and Administrator, opens Settings and checks at 1920, 1606, 1440, 1366, 1024, 768 and 360 px: the navy sidebar is visible from 960 px up with the logo once, there is no menu button on desktop, and below 960 px the sidebar is hidden and the menu button is visible. All pass.

Most likely cause of the screenshot: a Vite dev server started before `tailwind.config.js` was rewritten. Vite does not reload that file, so classes that depend on the custom 960 px `shell:` breakpoint (`tailwind.config.js:40`) and the token colours are missing from the served CSS: the sidebar keeps its base `hidden` class and the grid keeps one column. This was measured earlier on the user's own server on port 5173 (0 occurrences of `shell:hidden`, `shell:flex`, `.bg-accent` in the served CSS until the server was restarted). Fix: restart `npm run dev` after any change to `tailwind.config.js`.
