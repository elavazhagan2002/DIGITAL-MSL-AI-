# Screen inventory (baseline)

Source: `frontend/src/App.tsx` (NAV and page switch), `portal/*`, `components/*`. State: before any migration to the design-system kit.
Shot = before screenshot captured in `docs/ui-baseline/before/` (`<role>-<screen>-<mode>-1440x900.png`).

## Pre-login (all roles)
| Screen | Present | Where | Shot |
|---|---|---|---|
| Login (Sign in, role cards, demo prefill) | PRESENT | portal/LoginPage.tsx | public-login |
| Doctor registration step 1 Account | PRESENT | portal/RegisterWizard.tsx | public-register-1 |
| Registration step 2 Professional details | PRESENT | RegisterWizard.tsx | public-register-2 |
| Registration step 3 Therapeutic areas | PRESENT | RegisterWizard.tsx | public-register-3 |
| Registration step 4 Consent | PRESENT | RegisterWizard.tsx | public-register-4 |
| Contact verification (email and mobile codes) | PRESENT | portal/ContactVerification.tsx | not captured (needs a registered user; see PROGRESS) |
| MFA code / enrolment / recovery codes | PRESENT | portal/MfaSteps.tsx | not captured (admin MFA is exempt for demo accounts) |

## Doctor
| Screen | Present | Where | Shot |
|---|---|---|---|
| Talk to Digital MSL (chat, history, routing note, notice) | PRESENT | components/DigitalMSLChat.tsx | doctor-chat |
| Chat message types (note, answer, escalation, refusal/safety) | PRESENT | components/chat/MessageTypes.tsx | doctor-chat-notes (after "hi") |
| Pending verification panel | PRESENT | portal/VerificationPending.tsx | not captured (demo doctor is VERIFIED) |
| WhatsApp demo | PRESENT | portal/WhatsAppDemoChat.tsx | doctor-whatsapp-demo |
| Settings: profile, consent, certificate, security | PRESENT | SettingsPage.tsx + DoctorSettingsPanel.tsx + SecuritySettings.tsx | doctor-settings |
| No therapeutic area / Coming soon states | PRESENT (state) | App.tsx doctorChat() | not captured |

## Physical MSL
| Screen | Present | Where | Shot |
|---|---|---|---|
| My Queue (cases list) | PRESENT | components/MSLCasesPanel.tsx | msl-cases |
| Queue welcome dialog | PRESENT (modal) | App.tsx | msl-queue-welcome |
| Response workspace (draft, references, approve, send) | PRESENT | components/MSLResponseWorkspace.tsx | opened from a case; shot only if a case exists |
| Registered Doctors (table, filters) | PRESENT | portal/DoctorRegistryPage.tsx | msl-doctors |
| Doctor detail drawer | PRESENT (drawer) | DoctorRegistryPage.tsx | msl-doctors-drawer |
| Load Documents (import + documents) | PRESENT | MedicalLiteratureImport.tsx + DocumentsPanel.tsx | msl-literature |
| Settings: account, reply wording, security | PRESENT | SettingsPage.tsx | msl-settings |
| Escalation inbox with SLA and internal notes | NOT PRESENT | (cases list is the current queue) | n/a |
| Reply composer with attachments | NOT PRESENT | (workspace has no attachments) | n/a |
| Q&A Library | NOT PRESENT | | n/a |

## Administrator
| Screen | Present | Where | Shot |
|---|---|---|---|
| Home | PRESENT | portal/HomePage.tsx | admin-home |
| Registered Doctors + drawer (verify, reject, deactivate, certificate) | PRESENT | DoctorRegistryPage.tsx | admin-doctors |
| WhatsApp demo | PRESENT | WhatsAppDemoChat.tsx | admin-whatsapp-demo |
| Audit Trail | PRESENT | portal/AdminConversationsPage.tsx | admin-conversations |
| Settings > People (users, MSL teams) | PRESENT | portal/PeoplePage.tsx | admin-settings-people |
| Settings > Therapeutic areas (+ edit dialog) | PRESENT | portal/TherapeuticAreasPage.tsx | admin-settings-areas |
| Settings > Scope policy | PRESENT | SettingsPage.tsx | admin-settings-scope |
| Settings > Account / security | PRESENT | SettingsPage.tsx + SecuritySettings.tsx | admin-settings-account |
| Repositories list (admin) | PRESENT inside Load Documents (MSL) / areas; no separate admin page | | n/a |
| Q&A Library admin | NOT PRESENT | | n/a |

## Shared
| Item | Present |
|---|---|
| Toasts (de-duplicated) | PRESENT (contexts/ToastContext.tsx) |
| Notification bell dropdown | PRESENT (portal/NotificationBell.tsx) |
| Mobile drawer | PRESENT (components/shell/Sidebar.tsx) |
| Loading / empty / error cards | PRESENT (components/StatePanels.tsx) |
| 403 page | NOT PRESENT (API 403s become inline messages) |
| 404 page | NOT PRESENT (no router; unknown state falls back to the first nav item) |
| 500 / crash page (error boundary) | NOT PRESENT |

## Orphan code (no importer found by grep)
`portal/LibraryAccessPage.tsx`, `components/ResearchChat.tsx`, `components/ThinkingPanel.tsx`, `components/DocumentPreview.tsx`, `components/DoctorHistoryPanel.tsx`, `components/TypingIndicator.tsx`. `RepoTreePanel.tsx` and `SourcePdfViewer.tsx` are imported only by other components (check before deleting). The three type-error files (DocumentPreview, RepoTreePanel, ThinkingPanel) were already failing typecheck before this task.
