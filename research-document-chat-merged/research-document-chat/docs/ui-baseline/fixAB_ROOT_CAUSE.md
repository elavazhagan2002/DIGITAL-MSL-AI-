# Fix A + B: root causes (Doctor chat notice, Registered Doctors)

Written before the changes were made; evidence from the code, the API and the local database.

## A. Compliance notice and routing note

- **Cause of the space taken.** `DigitalMSLChat.tsx` rendered three non-scrolling (`shrink-0`) blocks inside the chat
  panel's flex column, above the scrolling thread: a two-line area header, the permanent routing banner
  (`routed-to-msl`, shown whenever `routedToMsl`), and `ComplianceBanner` (notice text clamped to two lines plus a
  version line and Read more). At 1440x900 these were about 200 px of a 743 px panel, always on screen, and they
  stayed there however long the doctor chatted.
- **Fix.** The notice is now the first item inside the scrolling thread (`NoticeCard`), shown until the doctor
  acknowledges the current notice version (Got it, or sending the first message). The acknowledgement is stored in
  `localStorage` per user and per version (`lib/noticeAck.ts`). A `Notice` link in a one-line area header opens the
  full text in a modal; a one-line disclaimer sits under the composer; the permanent routing banner is gone (its
  sentence is inside the first-conversation notice, and the Escalation message says a Physical MSL will review).
- **Follow-up (no backend in this task).** A server-side record of the notice acknowledgement (user, version, time).

## B. Registered Doctors

1. **Where repository names leak into the Areas column: data, not the mapping.**
   `GET /doctors` already returns `areas: [{repository_id, name, is_default}]`, resolved from the therapeutic-area
   table. In the local database (`backend/research_chat.db`) three rows of `dmsl_therapeutic_areas` are named after IKM
   repositories, with `sort_order = 0` and no description: `Eye_Core_Retina-222e`, `General_Research_Repository`,
   `Retina-Product-A`. They were created by the old `seed_demo_doctor_library()` from the IKM repository list
   (`name=lib["name"]`) and are assigned to the legacy demo doctors (`doctor@digital-msl.local`, `meera@...`,
   `vikram@...`). The page showed exactly what the table holds, and when an id was not found it fell back to the raw id.
   - Display-layer fix only (`lib/areaNames.ts`): names that look like repository identifiers are never shown as areas,
     raw ids are never shown, the default area is first, a doctor with no real area shows "No area".
   - **Backend follow-ups (not changed here):** rename or deactivate those three legacy rows; stop
     `seed_demo_doctor_library()` creating therapeutic areas from IKM repository names.
2. **Clipped pagination.** Not reproducible in the current build: the page scrolls inside `#main-content`; the footer
   is outside that scroll region (`AppShell`), and the old pagination row sat after a 1100 px-wide table, reachable by
   scrolling (measured: content height 1747 px, pagination bottom inside the scroll content). The clipped row in the
   report most likely came from the stale dev server that was missing the shell breakpoint classes (see
   `docs/ui-baseline/` notes). The redesign puts the count and pagination inside the card and a test scrolls to the end
   and asserts the pagination row is fully inside the viewport and above the footer at six viewports.
3. **Layout causes of the clumsy table.** Eight columns with `min-w-[1100px]`, `table-layout: auto` (long names and
   e-mails widened columns), pills that were allowed to wrap, e-mails with `break-words`. Now six columns on a fixed
   layout, one-line pills, truncation with a title tooltip.
4. **Drawer offset (found while building).** The old drawer, and any `position: fixed` overlay rendered inside the page
   content, was captured by an ancestor (its scrim covered only the main column and started below the top edge).
   Drawer, Modal and Popover now render in a portal on `document.body`.
5. **True duplicates.** None. In the local database, e-mail, registration-number index and mobile index are all unique.
   Two doctors share the display name "Dr. Arjun Mehta" with different e-mails (`doctor@digital-msl.local`,
   `meera@digital-msl.local`); that is valid and the e-mail is shown to tell them apart.

## NOT PRESENT (needs the backend or types; not changed here)

- Consent history with dates and the notice version for staff: the doctor-detail API only returns the current yes/no
  per channel. The drawer says so.
