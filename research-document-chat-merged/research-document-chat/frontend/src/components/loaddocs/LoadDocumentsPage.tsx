import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Info,
  Loader2,
  Lock,
  RefreshCw,
  Search,
  ServerCrash,
  ShieldAlert,
  Settings2,
  X,
} from 'lucide-react';
import { FileDropzone } from '../ui/FileDropzone';
import { StateCard, ErrorCard } from '../StatePanels';
import { friendlyError } from '../../lib/friendlyError';
import { useQuery } from '../../lib/queryCache';
import { useToast } from '../../contexts/ToastContext';
import { filterInputClass, inputClass, labelClass, primaryButton, secondaryButton, statusBadge, type BadgeTone } from '../../config/pageTheme';
import {
  ALLOWED_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  fetchLoadDocsContext,
  fetchLoadedDocuments,
  formatBytes,
  normalizeStatus,
  uploadFileWithProgress,
  validateFile,
  type IngestionStatus,
  type LoadDocsContext,
  type LoadDocsRepository,
  type LoadedDocument,
} from '../../services/loadDocumentsService';
import MedicalLiteratureImport from '../MedicalLiteratureImport';

const CONTEXT_KEY = 'load-docs-context';
const DOC_TYPES = ['Prescribing information', 'Guideline', 'Protocol', 'Publication', 'Other'];
const APPROVAL_STATUSES = ['DRAFT', 'IN_REVIEW', 'APPROVED'];

const STATUS_LABEL: Record<IngestionStatus, string> = { queued: 'Queued', processing: 'Processing', ready: 'Ready', failed: 'Failed' };
const STATUS_TONE: Record<IngestionStatus, BadgeTone> = { queued: 'slate', processing: 'cyan', ready: 'blue', failed: 'rose' };

function StatusPill({ status }: { status: IngestionStatus }) {
  return (
    <span className={`${statusBadge(STATUS_TONE[status])} whitespace-nowrap`} data-testid={`status-${status}`}>
      {status === 'processing' && <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

function Panel({ title, subtitle, action, children, testId }: { title: string; subtitle?: string; action?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section className="rounded-[14px] border border-line bg-card" data-testid={testId}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

function Notice({ tone, icon, children, testId }: { tone: 'info' | 'warn'; icon: ReactNode; children: ReactNode; testId?: string }) {
  const cls = tone === 'warn' ? 'border-warn-line bg-warn-bg text-warn-fg' : 'border-line bg-selbg text-fg';
  return (
    <div className={`flex items-start gap-3 rounded-[10px] border px-4 py-3 text-sm ${cls}`} role="status" data-testid={testId}>
      <span className="mt-0.5 shrink-0" aria-hidden="true">{icon}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** Skeleton rows from the kit: shown while the page decides which state it is in. */
function PageSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading Load Documents" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-[14px] border border-line bg-card p-5">
          <div className="h-4 w-1/3 animate-pulse rounded-md bg-soft" />
          <div className="mt-4 h-3 w-5/6 animate-pulse rounded-md bg-soft" />
          <div className="mt-2 h-3 w-2/3 animate-pulse rounded-md bg-soft" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- states that are not the working page
function UnavailableState({ ctx, isAdmin, onRetry, retrying }: { ctx: LoadDocsContext; isAdmin: boolean; onRetry: () => void; retrying: boolean }) {
  const retry = (
    <div className="flex flex-col items-center gap-2">
      <button type="button" className={primaryButton} onClick={onRetry} disabled={retrying}>
        {retrying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
        Retry
      </button>
      {ctx.request_id ? <p className="text-xs text-muted">Reference: {ctx.request_id}</p> : null}
    </div>
  );
  if (ctx.state === 'not_configured') {
    return (
      <StateCard
        testId="state-not-configured"
        icon={<Settings2 className="h-6 w-6" />}
        title="Document loading is not set up yet"
        text={isAdmin ? 'The document store address or credentials are missing.' : 'An administrator needs to finish setting up the document store.'}
        action={
          isAdmin && ctx.missing.length ? (
            <ul className="list-disc space-y-1 pl-5 text-left text-sm text-fg" data-testid="missing-list">
              {ctx.missing.map((m) => (
                <li key={m}>
                  <code className="rounded bg-soft px-1.5 py-0.5 text-xs">{m}</code>
                </li>
              ))}
            </ul>
          ) : undefined
        }
      />
    );
  }
  if (ctx.state === 'unauthorised') {
    return (
      <StateCard
        testId="state-unauthorised"
        icon={<Lock className="h-6 w-6" />}
        title="The document store did not accept our sign-in"
        text={isAdmin ? 'Check the service account credentials for the document store (RAG_BUILDER_USERNAME / RAG_BUILDER_PASSWORD).' : 'An administrator needs to check the document store sign-in.'}
        action={retry}
      />
    );
  }
  if (ctx.state === 'no_repositories') {
    return (
      <StateCard
        testId="state-no-repositories"
        icon={<ShieldAlert className="h-6 w-6" />}
        title="No repository is linked to your therapeutic areas yet"
        text="An administrator links a document repository to each therapeutic area (Settings, Therapeutic areas) and assigns you to it. Then it appears here."
      />
    );
  }
  // ikm_unreachable
  return (
    <StateCard
      tone="error"
      testId="state-unreachable"
      icon={<ServerCrash className="h-6 w-6" />}
      title="We can't reach the document store right now"
      text={isAdmin ? 'Check the document store address in the backend settings (RAG_BUILDER_BASE_URL) and that the server is running.' : "Documents can't be loaded until it is back. Please try again in a few minutes."}
      action={retry}
    />
  );
}

// ---------------------------------------------------------------- working page
interface UploadItem {
  id: string;
  file: File;
  percent: number;
  phase: 'validating' | 'uploading' | 'sent' | 'rejected' | 'failed';
  error?: string;
  requestId?: string;
  documentId?: string;
}

function RepositoryPicker({ repos, value, onChange }: { repos: LoadDocsRepository[]; value: string; onChange: (id: string) => void }) {
  return (
    <fieldset>
      <legend className="sr-only">Repository</legend>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" role="radiogroup" aria-label="Repository">
        {repos.map((repo) => {
          const active = repo.repository_id === value;
          return (
            <label
              key={repo.repository_id}
              className={`flex cursor-pointer flex-col gap-2 rounded-[12px] border-2 p-4 transition-colors ${active ? 'border-selb bg-selbg' : 'border-line bg-card hover:bg-soft'}`}
              data-testid="repo-card"
            >
              <input
                type="radio"
                name="repository"
                className="sr-only"
                checked={active}
                onChange={() => onChange(repo.repository_id)}
                aria-label={repo.name}
              />
              <span className="flex items-start justify-between gap-2">
                <span className="min-w-0 break-words text-sm font-semibold text-fg">{repo.name}</span>
                {active ? <CheckCircle2 className="h-4 w-4 shrink-0 text-link" aria-hidden="true" /> : null}
              </span>
              <span className="text-xs text-muted">{repo.therapeutic_area || 'No therapeutic area linked'}</span>
              <span className="flex flex-wrap items-center gap-2">
                <span className={statusBadge(repo.approved ? 'blue' : 'amber')}>{repo.approved ? 'Approved' : 'Not approved'}</span>
                <span className={statusBadge('slate')}>
                  {typeof repo.document_count === 'number' ? `${repo.document_count} document${repo.document_count === 1 ? '' : 's'}` : 'Count not available'}
                </span>
                {repo.demo ? <span className={statusBadge('amber')}>Demo data</span> : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function MetadataFields({ fileName }: { fileName: string }) {
  // The backend does not pass document metadata to the document store yet (NOT PRESENT): the form is shown
  // disabled with the reason, nothing is sent and nothing is faked.
  const base = fileName.replace(/\.[^.]+$/, '');
  return (
    <div className="mt-3 rounded-[10px] border border-line bg-soft p-3" data-testid="metadata-form">
      <p className="mb-3 flex items-start gap-2 text-xs text-muted">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        Document details are not sent to the document store yet, so these fields cannot be saved. Approval status is set by QA in the document store, not here.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <label className="block">
          <span className={labelClass}>Title</span>
          <input className={inputClass} disabled value={base} readOnly aria-describedby="meta-note" />
        </label>
        <label className="block">
          <span className={labelClass}>Document type</span>
          <select className={inputClass} disabled defaultValue={DOC_TYPES[0]}>
            {DOC_TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className={labelClass}>Version</span>
          <input className={inputClass} disabled placeholder="for example 2.0" />
        </label>
        <label className="block">
          <span className={labelClass}>Effective date</span>
          <input className={inputClass} disabled type="date" />
        </label>
        <label className="block">
          <span className={labelClass}>Expiry date</span>
          <input className={inputClass} disabled type="date" />
        </label>
        <label className="block">
          <span className={labelClass}>Approval status</span>
          <select className={inputClass} disabled defaultValue="DRAFT">
            {APPROVAL_STATUSES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}

function UploadRow({ item, docStatus, onRemove }: { item: UploadItem; docStatus?: IngestionStatus; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const failed = item.phase === 'rejected' || item.phase === 'failed';
  const status: IngestionStatus | undefined = item.phase === 'sent' ? docStatus ?? 'queued' : failed ? 'failed' : undefined;
  return (
    <li className="rounded-[12px] border border-line bg-card p-4" data-testid="upload-row">
      <div className="flex flex-wrap items-center gap-3">
        <FileText className="h-5 w-5 shrink-0 text-link" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-fg" title={item.file.name}>{item.file.name}</p>
          <p className="text-xs text-muted">{formatBytes(item.file.size)}</p>
        </div>
        {status ? <StatusPill status={status} /> : <span className={statusBadge('slate')}>Uploading</span>}
        <button type="button" className="flex h-11 w-11 items-center justify-center rounded-[10px] text-muted hover:bg-soft" onClick={onRemove} aria-label={`Remove ${item.file.name} from the list`}>
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      {item.phase === 'uploading' || item.phase === 'sent' ? (
        <div className="mt-3" aria-live="polite">
          <div className="h-2 overflow-hidden rounded-full bg-soft" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.percent} aria-label={`Upload progress for ${item.file.name}`}>
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${item.percent}%` }} />
          </div>
          <p className="mt-1 text-xs text-muted">{item.phase === 'sent' ? 'Sent. Waiting for the document store to process it.' : `Uploading ${item.percent}%`}</p>
        </div>
      ) : null}
      {failed ? (
        <p className="mt-3 flex items-start gap-2 text-sm text-err-fg" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {item.error}
            {item.requestId ? <span className="ml-1 text-xs text-muted">Reference: {item.requestId}</span> : null}
          </span>
        </p>
      ) : null}
      <button type="button" className="mt-3 text-xs font-semibold text-link underline-offset-2 hover:underline" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? 'Hide document details' : 'Document details (not available yet)'}
      </button>
      {open ? <MetadataFields fileName={item.file.name} /> : null}
    </li>
  );
}

function DocumentsTable({ repository }: { repository: LoadDocsRepository }) {
  const [docs, setDocs] = useState<LoadedDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof friendlyError> | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | IngestionStatus>('all');

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const result = await fetchLoadedDocuments(repository.repository_id);
        setDocs(result.documents);
        setError(null);
      } catch (err) {
        setError(friendlyError(err));
      } finally {
        setLoading(false);
      }
    },
    [repository.repository_id],
  );

  useEffect(() => {
    setDocs([]);
    load();
  }, [load]);

  const anyBusy = docs.some((d) => ['queued', 'processing'].includes(normalizeStatus(d.status)));
  useEffect(() => {
    if (!anyBusy) return;
    const t = setInterval(() => load(true), 4000);
    return () => clearInterval(t);
  }, [anyBusy, load]);

  // Let the upload list ask for an immediate refresh.
  useEffect(() => {
    const handler = () => load(true);
    window.addEventListener('dmsl:documents-changed', handler);
    return () => window.removeEventListener('dmsl:documents-changed', handler);
  }, [load]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return docs.filter((d) => {
      if (statusFilter !== 'all' && normalizeStatus(d.status) !== statusFilter) return false;
      return !q || (d.original_file_name || '').toLowerCase().includes(q);
    });
  }, [docs, search, statusFilter]);

  return (
    <Panel
      testId="documents-panel"
      title="Documents already loaded"
      subtitle={`${repository.name}${repository.demo ? ' (demo data)' : ''}`}
      action={
        <button type="button" className={secondaryButton} onClick={() => load()} aria-label="Refresh the document list">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" /> Refresh
        </button>
      }
    >
      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <label className="relative block flex-1">
          <span className="sr-only">Search documents</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden="true" />
          <input className={`${filterInputClass} w-full pl-9`} placeholder="Search by file name" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <label className="block sm:w-52">
          <span className="sr-only">Filter by status</span>
          <select className={`${filterInputClass} w-full`} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'all' | IngestionStatus)}>
            <option value="all">All statuses</option>
            {(Object.keys(STATUS_LABEL) as IngestionStatus[]).map((s) => (
              <option key={s} value={s}>{STATUS_LABEL[s]}</option>
            ))}
          </select>
        </label>
      </div>

      {error && !docs.length ? (
        <ErrorCard error={error} onRetry={() => load()} retrying={loading} />
      ) : loading && !docs.length ? (
        <div className="space-y-3" role="status" aria-label="Loading documents">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded-md bg-soft" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted" data-testid="no-documents">
          {docs.length ? 'No documents match your search.' : 'No documents have been loaded into this repository yet.'}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-[10px] border border-line">
          <table className="w-full min-w-[560px] border-separate border-spacing-0 text-sm" data-testid="documents-table">
            <thead>
              <tr className="bg-soft text-left text-xs uppercase tracking-wide text-muted">
                <th scope="col" className="px-4 py-3 font-semibold">File</th>
                <th scope="col" className="px-4 py-3 font-semibold">Type</th>
                <th scope="col" className="px-4 py-3 font-semibold">Loaded</th>
                <th scope="col" className="px-4 py-3 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => {
                const st = normalizeStatus(d.status);
                return (
                  <tr key={d.document_id} className="hover:bg-selbg" data-testid="document-row">
                    <td className="max-w-[360px] border-t border-line px-4 py-3">
                      <p className="truncate font-medium text-fg" title={d.original_file_name}>{d.original_file_name}</p>
                      {st === 'failed' && d.error_details ? <p className="mt-0.5 text-xs text-err-fg">{d.error_details}</p> : null}
                    </td>
                    <td className="border-t border-line px-4 py-3 text-muted">{d.document_type || 'Not stated'}</td>
                    <td className="whitespace-nowrap border-t border-line px-4 py-3 text-muted">
                      {d.upload_timestamp ? new Date(d.upload_timestamp * 1000).toLocaleDateString() : 'Not stated'}
                    </td>
                    <td className="border-t border-line px-4 py-3"><StatusPill status={st} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function WorkingPage({ ctx, isAdmin, onRefreshContext }: { ctx: LoadDocsContext; isAdmin: boolean; onRefreshContext: () => void }) {
  const toast = useToast();
  const [repoId, setRepoId] = useState(ctx.repositories[0]?.repository_id || '');
  const repository = ctx.repositories.find((r) => r.repository_id === repoId) || ctx.repositories[0];
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [docStatuses, setDocStatuses] = useState<Record<string, IngestionStatus>>({});
  const seq = useRef(0);

  useEffect(() => {
    if (!ctx.repositories.some((r) => r.repository_id === repoId)) setRepoId(ctx.repositories[0]?.repository_id || '');
  }, [ctx.repositories, repoId]);

  const patch = (id: string, change: Partial<UploadItem>) => setUploads((list) => list.map((u) => (u.id === id ? { ...u, ...change } : u)));

  // Follow the ingestion status of files we have sent.
  useEffect(() => {
    const pending = uploads.filter((u) => u.phase === 'sent' && u.documentId && docStatuses[u.documentId] !== 'ready' && docStatuses[u.documentId] !== 'failed');
    if (!pending.length || !repository) return;
    const t = setInterval(async () => {
      try {
        const result = await fetchLoadedDocuments(repository.repository_id);
        const next: Record<string, IngestionStatus> = {};
        result.documents.forEach((d) => {
          next[d.document_id] = normalizeStatus(d.status);
        });
        setDocStatuses((prev) => ({ ...prev, ...next }));
        window.dispatchEvent(new Event('dmsl:documents-changed'));
      } catch {
        /* the table shows its own error; keep polling quietly */
      }
    }, 3000);
    return () => clearInterval(t);
  }, [uploads, docStatuses, repository]);

  const addFiles = async (files: File[]) => {
    if (!repository) return;
    for (const file of files) {
      const id = `up-${++seq.current}`;
      const problem = validateFile(file);
      if (problem) {
        setUploads((list) => [...list, { id, file, percent: 0, phase: 'rejected', error: problem }]);
        continue;
      }
      setUploads((list) => [...list, { id, file, percent: 0, phase: 'uploading' }]);
      try {
        const result = await uploadFileWithProgress(repository.repository_id, file, (percent) => patch(id, { percent }));
        patch(id, { phase: 'sent', percent: 100, documentId: result.document_id });
        window.dispatchEvent(new Event('dmsl:documents-changed'));
        onRefreshContext(); // refetch keeps the page on screen (invalidate would drop it and the upload list)
      } catch (err) {
        const f = friendlyError(err, 'The file could not be uploaded. Please try again.');
        const message = f.status === 503 ? "We couldn't send the file to the document store. Please try again." : f.message;
        patch(id, { phase: 'failed', error: message, requestId: f.requestId });
        toast.error(message);
      }
    }
  };

  if (!repository) return null;
  const hint = `${ALLOWED_EXTENSIONS.join(', ')} · up to ${Math.round(MAX_UPLOAD_BYTES / 1024 / 1024)} MB per file`;
  return (
    <div className="space-y-5">
      {ctx.demo ? (
        <Notice tone="warn" icon={<Info className="h-4 w-4" />} testId="demo-banner">
          <strong className="font-semibold">Demo data.</strong> The real document store is not reachable, so this page shows a sample repository kept in memory.
          Nothing here is sent to the document store.
        </Notice>
      ) : null}
      {ctx.allow_list.empty ? (
        <Notice tone="info" icon={<Info className="h-4 w-4" />} testId="allow-list-note">
          No repository is on the approved list yet, so Digital MSL will not answer from any repository and sends every question to a Physical MSL.
          You can still load documents.{isAdmin ? ' An administrator sets the approved list (DIGITAL_MSL_APPROVED_REPOSITORY_IDS) once QA has agreed it.' : ''}
        </Notice>
      ) : null}

      <Panel title="Repository" subtitle="Choose where the documents go." testId="repository-panel">
        <RepositoryPicker repos={ctx.repositories} value={repository.repository_id} onChange={setRepoId} />
      </Panel>

      <Panel title="Load documents" subtitle={`Into: ${repository.name}`} testId="upload-panel">
        <FileDropzone onFiles={addFiles} accept={ALLOWED_EXTENSIONS.join(',')} hint={hint} />
        {uploads.length ? (
          <ul className="mt-4 space-y-3" data-testid="upload-list">
            {uploads.map((u) => (
              <UploadRow
                key={u.id}
                item={u}
                docStatus={u.documentId ? docStatuses[u.documentId] : undefined}
                onRemove={() => setUploads((list) => list.filter((x) => x.id !== u.id))}
              />
            ))}
          </ul>
        ) : null}
      </Panel>

      <DocumentsTable repository={repository} />

      {!repository.demo ? (
        <details className="rounded-[14px] border border-line bg-card" data-testid="literature-section">
          <summary className="cursor-pointer px-5 py-4 text-base font-semibold text-fg">Search literature to import (PubMed, ClinicalTrials.gov)</summary>
          <div className="border-t border-line p-5">
            <MedicalLiteratureImport
              repository={{ repository_id: repository.repository_id, name: repository.name }}
              onBack={() => undefined}
              onImported={onRefreshContext}
              showBack={false}
            />
          </div>
        </details>
      ) : (
        <Notice tone="info" icon={<Info className="h-4 w-4" />} testId="literature-disabled">
          Literature import (PubMed, ClinicalTrials.gov) needs the real document store and is disabled in demo data.
        </Notice>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- page
export function LoadDocumentsPage({ role }: { role: 'msl' | 'admin' }) {
  const query = useQuery<LoadDocsContext>(CONTEXT_KEY, fetchLoadDocsContext, { staleMs: 10_000 });
  const isAdmin = role === 'admin';

  let body: ReactNode;
  if (query.error && !query.data) {
    body =
      query.error.status === 403 ? (
        <StateCard testId="state-forbidden" icon={<Lock className="h-6 w-6" />} title="You don't have access to Load Documents" text="Loading documents is for Physical MSLs and administrators." />
      ) : (
        <ErrorCard error={query.error} onRetry={query.refetch} retrying={query.loading} title="We can't load this page right now" />
      );
  } else if (!query.data) {
    body = <PageSkeleton />;
  } else if (query.data.state !== 'ok') {
    body = <UnavailableState ctx={query.data} isAdmin={isAdmin} onRetry={query.refetch} retrying={query.loading} />;
  } else {
    body = <WorkingPage ctx={query.data} isAdmin={isAdmin} onRefreshContext={query.refetch} />;
  }

  return (
    <div className="mx-auto w-full max-w-[1200px] space-y-5" data-testid="load-documents-page">
      <div>
        <h2 className="text-lg font-semibold text-fg">Load documents</h2>
        <p className="mt-0.5 text-sm text-muted">Add approved documents to a therapeutic area's repository and follow them until they are ready.</p>
      </div>
      {body}
    </div>
  );
}
