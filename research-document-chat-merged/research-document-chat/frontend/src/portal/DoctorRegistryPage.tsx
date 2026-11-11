import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileText,
  Loader2,
  Mail,
  MessageCircle,
  RefreshCw,
  Search,
  Star,
  Users,
} from 'lucide-react';
import {
  deactivateDoctor,
  downloadDoctorCertificate,
  exportDoctorsCsv,
  getDoctor,
  listDoctors,
  listTherapeuticAreas,
  reactivateDoctor,
  setDoctorVerification,
  VERIFICATION_STATUSES,
  type DoctorListResult,
  type DoctorRegistryEntry,
  type TherapeuticArea,
  type VerificationStatus,
} from '../services/digitalMslService';
import { useAuth } from './AuthContext';
import { useToast } from '../contexts/ToastContext';
import { formatDateTimeIST } from '../utils/formatDateTimeIST';
import { verificationInfo } from '../utils/verificationStatus';
import { friendlyError, type FriendlyError } from '../lib/friendlyError';
import { looksLikeRepositoryName, shownAreas, type AreaRef, type ShownArea } from '../lib/areaNames';
import { ErrorCard, StateCard } from '../components/StatePanels';
import { Drawer } from '../components/ui/Drawer';
import { Popover } from '../components/ui/Popover';
import { filterInputClass, inputClass, labelClass, primaryButton, secondaryButton, statusBadge } from '../config/pageTheme';

const PAGE_SIZE = 20;

/** The list endpoint also returns the server-resolved `areas` (id, name, is_default). */
type Row = DoctorRegistryEntry & { areas?: AreaRef[]; basic_qualification?: string };

type SortKey = 'name' | 'verification_status' | 'registered_at';

const STATUS_LABEL: Record<string, string> = {
  VERIFIED: 'Verified',
  PENDING_REGISTRATION_VERIFICATION: 'Under review',
  NEEDS_INFO: 'Needs info',
  REJECTED: 'Rejected',
  PENDING_CONTACT_VERIFICATION: 'Contact pending',
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] || verificationInfo(status).label;
}

function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function dateOnlyIST(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric' });
}

/** One-line status badge; never wraps. */
function StatusPill({ status }: { status: string }) {
  return <span className={`${statusBadge(verificationInfo(status).tone)} whitespace-nowrap`}>{statusLabel(status)}</span>;
}

/** WhatsApp and Email as two small icons: filled when consent is granted, outlined when not. */
function ConsentIcons({ whatsapp, email }: { whatsapp: boolean; email: boolean }) {
  const icon = (label: string, on: boolean, node: React.ReactNode) => {
    const text = `${label} consent: ${on ? 'yes' : 'no'}`;
    return (
      <span
        role="img"
        aria-label={text}
        title={text}
        data-consent={on ? 'yes' : 'no'}
        className={`inline-flex h-7 w-7 items-center justify-center rounded-full border ${
          on ? 'border-accent bg-accent text-accentfg' : 'border-line bg-transparent text-muted'
        }`}
      >
        {node}
      </span>
    );
  };
  return (
    <span className="inline-flex items-center gap-1.5">
      {icon('WhatsApp', whatsapp, <MessageCircle className="h-3.5 w-3.5" aria-hidden="true" />)}
      {icon('Email', email, <Mail className="h-3.5 w-3.5" aria-hidden="true" />)}
    </span>
  );
}

function AreaChip({ area }: { area: ShownArea }) {
  return (
    <span className={`${statusBadge(area.isDefault ? 'cyan' : 'slate')} max-w-[210px] gap-1 whitespace-nowrap`} title={area.isDefault ? `${area.name} (default area)` : area.name}>
      {area.isDefault && <Star className="h-3 w-3 shrink-0 fill-current" aria-label="Default area" />}
      <span className="truncate">{area.name}</span>
    </span>
  );
}

/** First area as a chip, plus +N that opens a popover with the rest. */
function AreasCell({ areas }: { areas: ShownArea[] }) {
  if (areas.length === 0) return <span className="text-xs text-muted" data-testid="areas-cell">No area</span>;
  const [first, ...rest] = areas;
  return (
    <span className="flex items-center gap-1.5" data-testid="areas-cell">
      <AreaChip area={first} />
      {rest.length > 0 && (
        <Popover
          ariaLabel={`${rest.length} more therapeutic ${rest.length === 1 ? 'area' : 'areas'}`}
          testId="areas-popover"
          triggerClassName="inline-flex h-7 min-w-[2rem] items-center justify-center rounded-full border border-line bg-soft px-2 text-xs font-semibold text-fg hover:bg-selbg"
          trigger={`+${rest.length}`}
        >
          <ul className="space-y-1.5">
            {rest.map((a) => (
              <li key={a.id} className="flex items-center gap-1.5 text-sm text-fg">
                {a.isDefault && <Star className="h-3 w-3 shrink-0 fill-current text-link" aria-label="Default area" />}
                <span className="min-w-0 break-words">{a.name}</span>
              </li>
            ))}
          </ul>
        </Popover>
      )}
    </span>
  );
}

function Detail({ label, value, copyable }: { label: string; value?: string | number | null; copyable?: boolean }) {
  const text = value === undefined || value === null || value === '' ? '—' : String(value);
  return (
    <div className="grid grid-cols-3 gap-2 border-b border-line py-2 last:border-0">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="col-span-2 flex items-start gap-2 break-words text-sm text-fg">
        <span className="min-w-0 flex-1">{text}</span>
        {copyable && text !== '—' && (
          <button
            type="button"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-soft hover:text-fg"
            aria-label={`Copy ${label}`}
            onClick={() => navigator.clipboard?.writeText(text).catch(() => undefined)}
          >
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </dd>
    </div>
  );
}

const SKELETON_ROWS = [0, 1, 2, 3, 4, 5];

export function DoctorRegistryPage() {
  const { user } = useAuth();
  const toast = useToast();
  const isAdmin = user?.role === 'admin';

  const [areas, setAreas] = useState<TherapeuticArea[]>([]);
  const [result, setResult] = useState<DoctorListResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [exporting, setExporting] = useState(false);

  const [search, setSearch] = useState('');
  const [city, setCity] = useState('');
  const [status, setStatus] = useState('');
  const [repositoryId, setRepositoryId] = useState('');
  const [sort, setSort] = useState<SortKey>('registered_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebounced(search);
  const debouncedCity = useDebounced(city);

  const [selectedId, setSelectedId] = useState('');

  // Therapeutic areas for the filter: real areas only (never repository-named entries).
  const areaOptions = useMemo(
    () => areas.filter((a) => a.enabled !== false && a.active !== false && !looksLikeRepositoryName(a.name)),
    [areas],
  );

  const filters = useMemo(
    () => ({ q: debouncedSearch.trim(), city: debouncedCity.trim(), status, repositoryId, sort, order }),
    [debouncedSearch, debouncedCity, status, repositoryId, sort, order],
  );
  const filtered = !!(search || city || status || repositoryId);

  useEffect(() => {
    listTherapeuticAreas().then(setAreas).catch(() => setAreas([]));
  }, []);

  // A changed filter or sort starts again from the first page.
  useEffect(() => setPage(1), [filters]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await listDoctors({ ...filters, page, pageSize: PAGE_SIZE }));
    } catch (err) {
      setError(friendlyError(err, 'We could not load the registered doctors. Please try again.'));
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    load();
  }, [load]);

  const changeSort = (key: SortKey) => {
    if (key === sort) setOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    else {
      setSort(key);
      setOrder(key === 'registered_at' ? 'desc' : 'asc');
    }
  };

  const clearFilters = () => {
    setSearch('');
    setCity('');
    setStatus('');
    setRepositoryId('');
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      await exportDoctorsCsv(filters);
      toast.success('Export downloaded. This export is recorded in the audit trail.');
    } catch (err) {
      toast.error(friendlyError(err, 'Could not export.').message);
    } finally {
      setExporting(false);
    }
  };

  const counts = result?.counts;
  const total = result?.total ?? 0;
  const pageSize = result?.page_size || PAGE_SIZE;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const rows = (result?.doctors || []) as Row[];

  const strip: Array<{ label: string; value: number | string; filter: string; tone: string }> = [
    { label: 'Total', value: counts ? counts.total : '–', filter: '', tone: 'text-fg' },
    { label: 'Verified', value: counts ? counts.verified : '–', filter: 'VERIFIED', tone: 'text-ok-fg' },
    { label: 'Under review', value: counts ? counts.pending : '–', filter: 'PENDING_REGISTRATION_VERIFICATION', tone: 'text-link' },
    { label: 'Needs info', value: counts ? counts.needs_info : '–', filter: 'NEEDS_INFO', tone: 'text-warn-fg' },
    { label: 'Rejected', value: counts ? counts.rejected : '–', filter: 'REJECTED', tone: 'text-err-fg' },
  ];

  const sortHeader = (key: SortKey, label: string) => (
    <button type="button" className="inline-flex h-11 items-center gap-1 uppercase tracking-wide hover:text-fg" onClick={() => changeSort(key)}>
      {label}
      {key === sort && (order === 'asc' ? <ArrowUp className="h-3 w-3" aria-hidden="true" /> : <ArrowDown className="h-3 w-3" aria-hidden="true" />)}
    </button>
  );
  const ariaSort = (key: SortKey) => (key === sort ? (order === 'asc' ? ('ascending' as const) : ('descending' as const)) : undefined);

  const emptyState = (testId: string) => (
    <div className="px-4 py-14 text-center" data-testid={testId}>
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-selbg text-ok-fg" aria-hidden="true">
        <Users className="h-6 w-6" />
      </div>
      <h2 className="mt-3 text-base font-semibold text-fg">{filtered ? 'No doctors match these filters' : 'No doctors have registered yet'}</h2>
      <p className="mt-1 text-sm text-muted">
        {filtered ? 'Try a different search or clear the filters.' : 'Doctors appear here once they register and choose a therapeutic area.'}
      </p>
      {filtered && (
        <button type="button" className={`${secondaryButton} mt-4`} onClick={clearFilters}>
          Clear filters
        </button>
      )}
    </div>
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-4 pb-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold text-fg">Registered doctors</h1>
          <p className="mt-1 text-sm text-muted">
            {isAdmin
              ? 'Verify registrations against the National Medical Register and manage doctor access.'
              : 'Doctors in your therapeutic areas. This view is read-only; the administrator records verification decisions.'}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={secondaryButton} onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" /> Refresh
          </button>
          {isAdmin && (
            <button type="button" className={primaryButton} onClick={exportCsv} disabled={exporting || total === 0}>
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="h-4 w-4" aria-hidden="true" />}
              Export CSV
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by verification status" data-testid="status-strip">
        {strip.map((s) => {
          const active = status === s.filter;
          return (
            <button
              key={s.label}
              type="button"
              aria-pressed={active}
              onClick={() => setStatus(s.filter)}
              className={`flex h-11 items-center gap-2 rounded-xl border px-4 text-sm transition-colors ${
                active ? 'border-selb bg-selbg' : 'border-line bg-card hover:bg-soft'
              }`}
            >
              <span className={`text-lg font-bold tabular-nums ${s.tone}`}>{s.value}</span>
              <span className="font-medium text-muted">{s.label}</span>
            </button>
          );
        })}
      </div>

      <section className="overflow-hidden rounded-2xl border border-line bg-card" aria-label="Registered doctors" data-testid="doctors-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4" data-testid="doctors-filters">
          <div className="relative min-w-[220px] flex-1">
            <label htmlFor="doctor-search" className="sr-only">Search doctors</label>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden="true" />
            <input
              id="doctor-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, email, registration no."
              className={`${filterInputClass} !h-[42px] w-full pl-9 pr-3`}
            />
          </div>
          <div className="w-44">
            <label htmlFor="doctor-status" className="sr-only">Verification status</label>
            <select id="doctor-status" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className={`${filterInputClass} !h-[42px] w-full px-3`}>
              <option value="">All statuses</option>
              {VERIFICATION_STATUSES.map((s: VerificationStatus) => (
                <option key={s} value={s}>
                  {statusLabel(s)}
                </option>
              ))}
            </select>
          </div>
          <div className="w-56">
            <label htmlFor="doctor-area" className="sr-only">Therapeutic area</label>
            <select id="doctor-area" aria-label="Therapeutic area" value={repositoryId} onChange={(e) => setRepositoryId(e.target.value)} className={`${filterInputClass} !h-[42px] w-full px-3`}>
              <option value="">All therapeutic areas</option>
              {areaOptions.map((a) => (
                <option key={a.repository_id} value={a.repository_id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <label htmlFor="doctor-city" className="sr-only">City</label>
            <input id="doctor-city" value={city} onChange={(e) => setCity(e.target.value)} placeholder="City" className={`${filterInputClass} !h-[42px] w-full px-3`} />
          </div>
          {filtered && (
            <button type="button" onClick={clearFilters} className="inline-flex h-11 items-center px-1 text-sm font-semibold text-link underline-offset-2 hover:underline" data-testid="clear-filters">
              Clear filters
            </button>
          )}
          <p className="ml-auto whitespace-nowrap text-sm text-muted" aria-live="polite" data-testid="result-count">
            {error && !result ? '' : total === 0 ? 'No results' : `Showing ${from}-${to} of ${total}`}
          </p>
        </div>

        {error && !result ? (
          <div className="p-4">
            <ErrorCard error={error} onRetry={load} retrying={loading} />
          </div>
        ) : (
          <>
            {/* table from 640px up: horizontal scroll stays inside this card, never the page */}
            <div className="hidden max-h-[min(68vh,760px)] overflow-auto sm:block" data-testid="doctors-scroll">
              <table className="w-full min-w-[1090px] table-fixed border-separate border-spacing-0 text-left text-sm">
                <colgroup>
                  <col style={{ width: 280 }} />
                  <col style={{ width: 250 }} />
                  <col style={{ width: 240 }} />
                  <col style={{ width: 120 }} />
                  <col style={{ width: 90 }} />
                  <col style={{ width: 110 }} />
                </colgroup>
                <thead>
                  <tr className="text-xs uppercase tracking-wide text-muted">
                    {[
                      { label: 'Doctor', sortKey: 'name' as SortKey },
                      { label: 'Registration' },
                      { label: 'Areas' },
                      { label: 'Status', sortKey: 'verification_status' as SortKey },
                      { label: 'Consent' },
                      { label: 'Registered', sortKey: 'registered_at' as SortKey },
                    ].map((col) => (
                      <th
                        key={col.label}
                        scope="col"
                        aria-sort={col.sortKey ? ariaSort(col.sortKey) : undefined}
                        className="sticky top-0 z-10 whitespace-nowrap border-b border-line bg-soft px-4 py-0 font-semibold"
                      >
                        {col.sortKey ? sortHeader(col.sortKey, col.label) : <span className="inline-flex h-11 items-center">{col.label}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading && !result &&
                    SKELETON_ROWS.map((i) => (
                      <tr key={i} data-testid="doctor-skeleton-row" aria-hidden="true">
                        {[200, 180, 150, 90, 64, 90].map((w, c) => (
                          <td key={c} className="h-14 border-b border-line px-4">
                            <div className="h-3.5 animate-pulse rounded bg-line" style={{ width: w }} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  {rows.map((d) => {
                    const shown = shownAreas(d, areas);
                    const inactive = d.status && d.status !== 'active';
                    const reg = d.registration_number ? `${d.registration_number}${d.registration_council ? ` · ${d.registration_council}` : ''}` : '';
                    return (
                      <tr
                        key={d.user_id}
                        data-testid="doctor-row"
                        onClick={() => setSelectedId(d.user_id)}
                        className={`cursor-pointer hover:bg-selbg ${loading ? 'opacity-60' : ''}`}
                      >
                        <td className="h-14 max-w-[300px] border-b border-line px-4">
                          <button type="button" className="block w-full min-w-0 text-left" onClick={() => setSelectedId(d.user_id)} aria-haspopup="dialog">
                            <span className="block truncate font-semibold text-fg" title={d.display_name}>{d.display_name}</span>
                            <span className="block truncate text-xs text-muted" title={d.email}>{d.email}</span>
                          </button>
                        </td>
                        <td className="h-14 max-w-[280px] border-b border-line px-4">
                          {reg ? (
                            <span className="block truncate text-fg" title={reg}>{reg}</span>
                          ) : (
                            <span className="text-muted">Not provided</span>
                          )}
                        </td>
                        <td className="h-14 border-b border-line px-4">
                          <AreasCell areas={shown} />
                        </td>
                        <td className="h-14 whitespace-nowrap border-b border-line px-4">
                          {inactive ? (
                            <span className={`${statusBadge('rose')} whitespace-nowrap`} title={`Deactivated. Verification: ${statusLabel(d.verification_status)}`}>Deactivated</span>
                          ) : (
                            <StatusPill status={d.verification_status} />
                          )}
                        </td>
                        <td className="h-14 border-b border-line px-4">
                          <ConsentIcons whatsapp={d.consents.whatsapp_messaging} email={d.consents.email_messaging} />
                        </td>
                        <td className="h-14 whitespace-nowrap border-b border-line px-4 text-fg" title={d.registered_at ? formatDateTimeIST(d.registered_at) : undefined}>
                          {dateOnlyIST(d.registered_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {result && rows.length === 0 && !loading && emptyState("doctors-empty")}
            </div>

            {/* below 640px every row is a stacked card */}
            <ul className="divide-y divide-line sm:hidden" data-testid="doctor-cards">
              {loading && !result &&
                SKELETON_ROWS.slice(0, 3).map((i) => (
                  <li key={i} className="space-y-2 p-4" aria-hidden="true">
                    <div className="h-4 w-40 animate-pulse rounded bg-line" />
                    <div className="h-3 w-56 animate-pulse rounded bg-line" />
                  </li>
                ))}
              {rows.map((d) => {
                const shown = shownAreas(d, areas);
                const inactive = d.status && d.status !== 'active';
                return (
                  <li key={d.user_id}>
                    <button type="button" data-testid="doctor-card" onClick={() => setSelectedId(d.user_id)} aria-haspopup="dialog" className="block w-full space-y-2 p-4 text-left hover:bg-selbg">
                      <span className="flex items-start justify-between gap-2">
                        <span className="min-w-0">
                          <span className="block truncate font-semibold text-fg">{d.display_name}</span>
                          <span className="block truncate text-xs text-muted">{d.email}</span>
                        </span>
                        {inactive ? <span className={`${statusBadge('rose')} whitespace-nowrap`}>Deactivated</span> : <StatusPill status={d.verification_status} />}
                      </span>
                      <span className="block truncate text-xs text-muted">
                        {d.registration_number ? `${d.registration_number}${d.registration_council ? ` · ${d.registration_council}` : ''}` : 'Registration not provided'}
                      </span>
                      <span className="flex flex-wrap items-center gap-1.5">
                        {shown.length === 0 ? <span className="text-xs text-muted">No area</span> : shown.map((a) => <AreaChip key={a.id} area={a} />)}
                      </span>
                      <span className="flex items-center justify-between text-xs text-muted">
                        <ConsentIcons whatsapp={d.consents.whatsapp_messaging} email={d.consents.email_messaging} />
                        <span>Registered {dateOnlyIST(d.registered_at)}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {result && rows.length === 0 && !loading && <div className="sm:hidden">{emptyState("doctors-empty-cards")}</div>}
            {error && result && (
              <div className="border-t border-line p-4">
                <ErrorCard error={error} onRetry={load} retrying={loading} />
              </div>
            )}
          </>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3" data-testid="doctors-pagination">
          <p className="text-sm text-muted" data-testid="page-indicator">
            Page {page} of {lastPage}
          </p>
          <div className="flex items-center gap-2">
            <button type="button" className={secondaryButton} disabled={page <= 1 || loading} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <ChevronLeft className="h-4 w-4" aria-hidden="true" /> Previous
            </button>
            <button type="button" className={secondaryButton} disabled={page >= lastPage || loading} onClick={() => setPage((p) => Math.min(lastPage, p + 1))}>
              Next <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>

      {selectedId && (
        <DoctorDrawer userId={selectedId} isAdmin={isAdmin} areas={areas} onClose={() => setSelectedId('')} onChanged={load} />
      )}
    </div>
  );
}

type AdminAction = 'verify' | 'reject' | 'needs_info' | 'deactivate' | 'reactivate';

function DoctorDrawer({
  userId,
  isAdmin,
  areas,
  onClose,
  onChanged,
}: {
  userId: string;
  isAdmin: boolean;
  areas: TherapeuticArea[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [doctor, setDoctor] = useState<Row | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState('');
  const [busy, setBusy] = useState<AdminAction | ''>('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDoctor((await getDoctor(userId)) as Row);
    } catch (err) {
      setError(friendlyError(err, 'We could not load this doctor. Please try again.'));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (action: AdminAction) => {
    const needsReason = action === 'reject' || action === 'needs_info' || action === 'deactivate';
    if (needsReason && reason.trim().length < 3) {
      setReasonError('A reason is required (at least 3 characters).');
      return;
    }
    setReasonError('');
    setBusy(action);
    try {
      let updated: DoctorRegistryEntry;
      if (action === 'verify') updated = await setDoctorVerification(userId, 'VERIFIED', reason.trim());
      else if (action === 'reject') updated = await setDoctorVerification(userId, 'REJECTED', reason.trim());
      else if (action === 'needs_info') updated = await setDoctorVerification(userId, 'NEEDS_INFO', reason.trim());
      else if (action === 'deactivate') updated = await deactivateDoctor(userId, reason.trim());
      else updated = await reactivateDoctor(userId, reason.trim());
      setDoctor((current) => ({ ...(current as Row), ...updated }));
      setReason('');
      toast.success('Saved. The decision is recorded in the audit trail.');
      onChanged();
    } catch (err) {
      toast.error(friendlyError(err, 'Could not save.').message);
    } finally {
      setBusy('');
    }
  };

  const openCertificate = async () => {
    try {
      await downloadDoctorCertificate(userId);
    } catch (err) {
      toast.error(friendlyError(err, 'Could not open the certificate.').message);
    }
  };

  const inactive = !!doctor && doctor.status !== 'active';
  const cert = doctor?.certificate;
  const shown = doctor ? shownAreas(doctor, areas) : [];

  return (
    <Drawer
      open
      onClose={onClose}
      title={doctor?.display_name || 'Loading…'}
      subtitle="Registered doctor"
      testId="doctor-drawer"
      badges={
        doctor ? (
          <>
            <StatusPill status={doctor.verification_status} />
            {inactive && <span className={statusBadge('rose')}>Deactivated</span>}
          </>
        ) : null
      }
    >
      <div className="space-y-5 p-5">
        {loading && !doctor && (
          <p className="flex items-center gap-2 text-sm text-muted" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading…
          </p>
        )}
        {error && <ErrorCard error={error} onRetry={load} retrying={loading} />}

        {doctor && (
          <>
            <section>
              <h3 className="text-sm font-semibold text-fg">Registration</h3>
              <dl className="mt-1">
                <Detail label="Registration no." value={doctor.registration_number} copyable />
                <Detail label="Council" value={doctor.registration_council} />
                <Detail label="Year" value={doctor.registration_year} />
                <Detail label="NMC Unique ID" value={doctor.nmr_id} copyable />
                <Detail label="Qualification" value={doctor.basic_qualification} />
                <Detail label="Highest" value={doctor.highest_qualification} />
                <Detail label="Speciality" value={doctor.speciality} />
              </dl>
              <p className="mt-1 text-[11px] text-muted">Check the registration against the public NMC register before verifying.</p>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-fg">Contact and practice</h3>
              <dl className="mt-1">
                <Detail label="Email" value={doctor.email} copyable />
                <Detail label="Mobile" value={doctor.mobile} copyable />
                <Detail label="Institution" value={doctor.institution} />
                <Detail label="City" value={doctor.city} />
                <Detail label="State" value={doctor.state} />
                <Detail label="PIN code" value={doctor.pincode} />
                <Detail label="Registered" value={doctor.registered_at ? formatDateTimeIST(doctor.registered_at) : null} />
                <Detail label="Last contact" value={doctor.last_contact_at ? formatDateTimeIST(doctor.last_contact_at) : 'Never'} />
              </dl>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-fg">Therapeutic areas</h3>
              <div className="mt-2 flex flex-wrap gap-1.5" data-testid="drawer-areas">
                {shown.map((a) => (
                  <AreaChip key={a.id} area={a} />
                ))}
                {shown.length === 0 && <span className="text-sm text-muted">No therapeutic area</span>}
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-fg">Consent</h3>
              <div className="mt-2 flex items-center gap-3 text-sm text-fg">
                <ConsentIcons whatsapp={doctor.consents.whatsapp_messaging} email={doctor.consents.email_messaging} />
                <span className="text-muted">
                  WhatsApp {doctor.consents.whatsapp_messaging ? 'granted' : 'not given'} · Email {doctor.consents.email_messaging ? 'granted' : 'not given'}
                </span>
              </div>
              <p className="mt-2 text-[11px] text-muted" data-testid="consent-history-note">
                Consent history with dates and the notice version is not available to staff yet.
              </p>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-fg">Activity</h3>
              <dl className="mt-1">
                <Detail label="Conversations" value={doctor.conversation_count ?? 0} />
                <Detail label="Escalations" value={doctor.escalation_count ?? 0} />
              </dl>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-fg">Verification</h3>
              <dl className="mt-1">
                <Detail label="Status" value={statusLabel(doctor.verification_status)} />
                <Detail label="Recorded by" value={doctor.verified_by_name} />
                <Detail label="Recorded" value={doctor.verified_at ? formatDateTimeIST(doctor.verified_at) : null} />
                <Detail label="Reason / notes" value={doctor.rejection_reason || doctor.verification_notes} />
                <Detail label="Certificate" value={cert?.present ? `Uploaded${cert.scan_status ? ` (scan: ${cert.scan_status})` : ''}` : 'Not uploaded'} />
              </dl>
              {isAdmin && cert?.present && (
                <button type="button" className={`${secondaryButton} mt-2`} onClick={openCertificate}>
                  <FileText className="h-4 w-4" aria-hidden="true" /> Open certificate
                </button>
              )}
              {!isAdmin && <p className="mt-1 text-[11px] text-muted">The certificate is visible to administrators only.</p>}
            </section>

            {isAdmin ? (
              <section className="rounded-xl border border-line p-4">
                <h3 className="text-sm font-semibold text-fg">Decision</h3>
                <label htmlFor="decision-reason" className={`${labelClass} mt-2`}>
                  Reason (required to reject, request information or deactivate)
                </label>
                <textarea
                  id="decision-reason"
                  rows={3}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  aria-invalid={!!reasonError}
                  aria-describedby={reasonError ? 'decision-reason-error' : undefined}
                  className={inputClass}
                />
                {reasonError && (
                  <p id="decision-reason-error" className="mt-1 flex items-center gap-1 text-xs text-err-fg" role="alert">
                    <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" /> {reasonError}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className={primaryButton} disabled={busy !== '' || doctor.verification_status === 'VERIFIED'} onClick={() => act('verify')}>
                    {busy === 'verify' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Verify
                  </button>
                  <button type="button" className={secondaryButton} disabled={busy !== ''} onClick={() => act('needs_info')}>
                    Request more info
                  </button>
                  <button type="button" className={secondaryButton} disabled={busy !== ''} onClick={() => act('reject')}>
                    Reject
                  </button>
                  {inactive ? (
                    <button type="button" className={secondaryButton} disabled={busy !== ''} onClick={() => act('reactivate')}>
                      Reactivate
                    </button>
                  ) : (
                    <button type="button" className={secondaryButton} disabled={busy !== ''} onClick={() => act('deactivate')}>
                      Deactivate
                    </button>
                  )}
                </div>
              </section>
            ) : (
              <p className="rounded-lg border border-line bg-soft px-3 py-2 text-sm text-muted">
                Read-only for Physical MSL. Verification decisions are made by the administrator.
              </p>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}

export { StateCard };
