import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ChevronDown, HelpCircle, Loader2, Search, X } from 'lucide-react';
import {
  importMedicalLiterature,
  isAbortError,
  searchMedicalLiterature,
  type LiteraturePreview,
} from '../services/medicalLiteracyService';
import {
  TRANSIENT_CONNECTOR_OPTIONS,
  repositoryConfiguredConnectors,
  type Repository,
} from '../services/repositoryService';
import { useToast } from '../contexts/ToastContext';
import {
  cardSurface,
  filterInputClass,
  primaryButton,
  secondaryButton,
} from '../config/pageTheme';

const MAX_RESULTS_PER_CONNECTOR = 100;

interface MedicalLiteratureImportProps {
  repository: Repository;
  onBack: () => void;
  onImported: () => void;
  showBack?: boolean;
  repositories?: Repository[];
  onSelectRepo?: (id: string) => void;
}

function connectorLabel(id: string): string {
  return TRANSIENT_CONNECTOR_OPTIONS.find((option) => option.id === id)?.label || id;
}

function pubmedUrl(item: LiteraturePreview): string | null {
  if (item.source !== 'PubMed') return null;
  const pmid = String(item.record_id || '').trim();
  if (item.url && /pubmed\.ncbi\.nlm\.nih\.gov/i.test(item.url)) return item.url;
  if (pmid) return `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`;
  return null;
}

function pmcUrl(item: LiteraturePreview): string | null {
  if (item.source !== 'PubMed') return null;
  if (item.pmc_url) return item.pmc_url;
  const raw = String(item.pmcid || '').trim();
  if (!raw) return null;
  const pmcid = raw.toUpperCase().startsWith('PMC') ? raw.toUpperCase() : `PMC${raw}`;
  return `https://www.ncbi.nlm.nih.gov/pmc/articles/${pmcid}/`;
}

function clinicalTrialsUrl(item: LiteraturePreview): string | null {
  if (item.source !== 'ClinicalTrials.gov') return null;
  if (item.url && /clinicaltrials\.gov/i.test(item.url)) return item.url;
  const nct = String(item.record_id || '').trim().toUpperCase();
  if (/^NCT\d+$/i.test(nct)) return `https://clinicaltrials.gov/study/${nct}`;
  return null;
}

function OpenLink({ href, label = 'Open' }: { href: string | null; label?: string }) {
  if (!href) return <span className="text-muted">—</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-link hover:underline"
    >
      {label}
    </a>
  );
}

function AccuracyBadge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  const tone =
    pct >= 90
      ? 'bg-selbg text-ok-fg'
      : pct >= 70
        ? 'bg-selbg text-ok-fg'
        : pct >= 40
          ? 'bg-warn-bg text-warn-fg'
          : 'bg-soft text-muted';
  return (
    <span className={`inline-flex min-w-[3.25rem] justify-center rounded-md px-2 py-0.5 text-xs font-semibold tabular-nums ${tone}`}>
      {pct}%
    </span>
  );
}

function formatPublishedDate(value?: string): string {
  const raw = String(value || '').trim();
  if (!raw) return '—';
  // Keep NCBI-style dates (e.g. 2026 Mar, 2024-12-01) readable as returned.
  if (/^(COMPLETED|RECRUITING|NOT_YET|UNKNOWN|ACTIVE|TERMINATED|WITHDRAWN|SUSPENDED)/i.test(raw)) {
    return '—';
  }
  return raw;
}

function HelpTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle" title={text}>
      <HelpCircle className="h-3.5 w-3.5 text-muted" aria-hidden />
      <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 hidden w-56 -translate-x-1/2 rounded-md bg-navy px-2 py-1 text-[11px] font-normal text-onnavy shadow-lg group-hover:block">
        {text}
      </span>
    </span>
  );
}

export default function MedicalLiteratureImport({
  repository,
  onBack,
  onImported,
  showBack = true,
  repositories = [],
  onSelectRepo,
}: MedicalLiteratureImportProps) {
  const toast = useToast();
  const configured = useMemo(
    () => repositoryConfiguredConnectors(repository),
    [repository]
  );
  const literatureConnectors = useMemo(
    () => configured.filter((id) => id === 'pubmed' || id === 'clinicaltrials'),
    [configured]
  );

  const [selectedConnectors, setSelectedConnectors] = useState<string[]>(literatureConnectors);
  const [connectorMenuOpen, setConnectorMenuOpen] = useState(false);
  const connectorMenuRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [freeFullTextOnly, setFreeFullTextOnly] = useState(true);
  const [formatAsMesh, setFormatAsMesh] = useState(false);
  const [pubmedMax, setPubmedMax] = useState(50);
  const [trialsMax, setTrialsMax] = useState(25);
  const searchAbortRef = useRef<AbortController | null>(null);
  const importAbortRef = useRef<AbortController | null>(null);
  const [searching, setSearching] = useState(false);
  const [importing, setImporting] = useState(false);
  const [results, setResults] = useState<LiteraturePreview[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [searchErrors, setSearchErrors] = useState<string[]>([]);
  const [hasSearched, setHasSearched] = useState(false);
  const skipNextFormReset = useRef(false);

  const clearResultsPanel = () => {
    setResults([]);
    setSelectedKeys(new Set());
    setSearchErrors([]);
    setHasSearched(false);
  };

  useEffect(() => {
    return () => {
      searchAbortRef.current?.abort();
      importAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    setSelectedConnectors(literatureConnectors);
    clearResultsPanel();
  }, [literatureConnectors]);

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (!connectorMenuRef.current?.contains(event.target as Node)) {
        setConnectorMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  useEffect(() => {
    if (skipNextFormReset.current) {
      skipNextFormReset.current = false;
      return;
    }
    if (!hasSearched) return;
    clearResultsPanel();
  }, [query, selectedConnectors, freeFullTextOnly, formatAsMesh, pubmedMax, trialsMax, hasSearched]);

  const bump = (value: number, delta: number) =>
    Math.max(1, Math.min(MAX_RESULTS_PER_CONNECTOR, value + delta));

  const handleSearch = async () => {
    if (!query.trim()) {
      toast.warning('Enter a search query.');
      return;
    }
    if (selectedConnectors.length === 0) {
      toast.warning('Select at least one connector for this search.');
      return;
    }

    setSearching(true);
    clearResultsPanel();
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    try {
      const response = await searchMedicalLiterature(
        {
          repository_id: repository.repository_id,
          query: query.trim(),
          connectors: selectedConnectors,
          free_full_text_only: freeFullTextOnly,
          format_terms_as_mesh: formatAsMesh,
          pubmed_max_results: pubmedMax,
          clinicaltrials_max_results: trialsMax,
        },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      const nextResults = response.results || [];
      skipNextFormReset.current = true;
      setResults(nextResults);
      setHasSearched(true);
      setSelectedKeys(
        new Set(
          nextResults
            .filter((item) => item.source !== 'PubMed' || item.has_pmc_pdf)
            .map((item) => item.selection_key)
        )
      );
      setSearchErrors(response.errors || []);
      if (nextResults.length === 0) {
        toast.warning('No new publications found for this query.');
      } else {
        toast.success(`Found ${nextResults.length} publication(s).`);
      }
      for (const err of response.errors || []) {
        toast.warning(err);
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        return;
      }
      clearResultsPanel();
      toast.error(error instanceof Error ? error.message : 'Literature search failed.');
    } finally {
      if (searchAbortRef.current === controller) {
        searchAbortRef.current = null;
      }
      if (!controller.signal.aborted) {
        setSearching(false);
      }
    }
  };

  const toggleKey = (key: string, disabled: boolean) => {
    if (disabled) return;
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectableResults = results.filter(
    (item) => item.source !== 'PubMed' || item.has_pmc_pdf
  );

  const handleImport = async () => {
    const selections = results.filter((item) => selectedKeys.has(item.selection_key));
    if (selections.length === 0) {
      toast.warning('Select at least one publication to import.');
      return;
    }

    setImporting(true);
    importAbortRef.current?.abort();
    const controller = new AbortController();
    importAbortRef.current = controller;
    try {
      const response = await importMedicalLiterature(
        {
          repository_id: repository.repository_id,
          selections,
        },
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      if (response.uploaded_count > 0) {
        toast.success(
          `Imported ${response.uploaded_count} document(s) into ${repository.name}.`
        );
        onImported();
      } else {
        toast.warning('No documents were imported.');
      }
      if (response.skipped_count > 0) {
        toast.warning(`Skipped ${response.skipped_count} already-imported record(s).`);
      }
      for (const err of response.errors || []) {
        toast.warning(err);
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        return;
      }
      toast.error(error instanceof Error ? error.message : 'Literature import failed.');
    } finally {
      if (importAbortRef.current === controller) {
        importAbortRef.current = null;
      }
      if (!controller.signal.aborted) {
        setImporting(false);
      }
    }
  };

  const showPubmedMax = selectedConnectors.includes('pubmed');
  const showTrialsMax = selectedConnectors.includes('clinicaltrials');
  const showResults = hasSearched && results.length > 0;
  const labelClass = 'mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted';

  return (
    <div className="w-full space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">Import clinical literature</h2>
          <p className="text-xs text-muted mt-0.5">PubMed & ClinicalTrials.gov → {repository.name}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {onSelectRepo && repositories.length > 1 && (
            <select
              value={repository.repository_id}
              onChange={(e) => onSelectRepo(e.target.value)}
              className={`${filterInputClass} text-xs`}
              aria-label="Therapeutic area repository"
            >
              {repositories.map((repo) => (
                <option key={repo.repository_id} value={repo.repository_id}>
                  {repo.name}
                </option>
              ))}
            </select>
          )}
          {showBack && (
            <button type="button" onClick={onBack} className={`${secondaryButton} inline-flex items-center gap-2 px-3 py-1.5`}>
              <ArrowLeft className="h-4 w-4" />
              My Queue
            </button>
          )}
        </div>
      </div>

      <div className={`${cardSurface} space-y-4 p-4`}>
        <div ref={connectorMenuRef} className="relative">
          <label className={labelClass}>
            Connectors for this search
            <HelpTip text="Only connectors enabled on this repository are listed." />
          </label>
          <button
            type="button"
            disabled={literatureConnectors.length === 0}
            onClick={() => setConnectorMenuOpen((open) => !open)}
            className={`${filterInputClass} flex min-h-11 w-full flex-wrap items-center gap-2 px-3 text-left`}
          >
            {selectedConnectors.length === 0 ? (
              <span className="text-muted">Choose connectors...</span>
            ) : (
              selectedConnectors.map((id) => (
                <span
                  key={id}
                  className="inline-flex items-center gap-1 rounded-md bg-accent px-2 py-0.5 text-xs font-semibold text-accentfg"
                >
                  {connectorLabel(id)}
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Remove ${connectorLabel(id)}`}
                    className="rounded hover:bg-accent"
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedConnectors((prev) => prev.filter((item) => item !== id));
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        setSelectedConnectors((prev) => prev.filter((item) => item !== id));
                      }
                    }}
                  >
                    <X className="h-3 w-3" />
                  </span>
                </span>
              ))
            )}
            <ChevronDown className="ml-auto h-4 w-4 shrink-0 text-muted" />
          </button>
          {connectorMenuOpen && (
            <div className="absolute z-30 mt-1 w-full rounded-lg border border-line bg-card py-1 shadow-lg">
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-sm text-fg hover:bg-soft"
                onClick={() =>
                  setSelectedConnectors(
                    selectedConnectors.length === literatureConnectors.length
                      ? []
                      : [...literatureConnectors]
                  )
                }
              >
                {selectedConnectors.length === literatureConnectors.length
                  ? 'Clear all'
                  : 'Select all'}
              </button>
              {literatureConnectors.map((id) => {
                const active = selectedConnectors.includes(id);
                return (
                  <button
                    key={id}
                    type="button"
                    className={`block w-full px-3 py-2 text-left text-sm ${
                      active
                        ? 'bg-selbg font-semibold text-ok-fg'
                        : 'text-fg hover:bg-soft'
                    }`}
                    onClick={() =>
                      setSelectedConnectors((prev) =>
                        prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
                      )
                    }
                  >
                    {connectorLabel(id)}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="inline-flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-line text-link focus:ring-selb"
              checked={freeFullTextOnly}
              onChange={(event) => setFreeFullTextOnly(event.target.checked)}
            />
            Free full text only
            <HelpTip text="PubMed always returns PDF-ready (PMC) articles up to your max. This filter prefers free full text when scanning NCBI." />
          </label>
          <label className="inline-flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-line text-link focus:ring-selb"
              checked={formatAsMesh}
              onChange={(event) => setFormatAsMesh(event.target.checked)}
            />
            Format terms as MeSH
            <HelpTip text="When off, searches plain terms like heart. When on, wraps terms as MeSH queries." />
          </label>
        </div>

        <div>
          <label className={labelClass}>
            Search query
            <HelpTip text='Plain PubMed search (e.g. heart) or MeSH (e.g. "Heart Diseases"[MeSH Terms]).' />
          </label>
          <input
            className={`${filterInputClass} w-full px-3`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="heart"
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                void handleSearch();
              }
            }}
          />
        </div>

        {(showPubmedMax || showTrialsMax) && (
          <div className="grid gap-3 sm:grid-cols-2">
            {showPubmedMax && (
              <div>
                <label className={labelClass}>
                  PubMed max results
                  <HelpTip text={`Fetch up to ${MAX_RESULTS_PER_CONNECTOR} PubMed results per search.`} />
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => setPubmedMax((v) => bump(v, -5))}
                  >
                    −
                  </button>
                  <input
                    type="number"
                    min={1}
                    max={MAX_RESULTS_PER_CONNECTOR}
                    className={`${filterInputClass} w-full px-3 text-center`}
                    value={pubmedMax}
                    onChange={(event) =>
                      setPubmedMax(
                        Math.max(
                          1,
                          Math.min(MAX_RESULTS_PER_CONNECTOR, Number(event.target.value) || 1)
                        )
                      )
                    }
                  />
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => setPubmedMax((v) => bump(v, 5))}
                  >
                    +
                  </button>
                </div>
              </div>
            )}
            {showTrialsMax && (
              <div>
                <label className={labelClass}>
                  ClinicalTrials.gov max results
                  <HelpTip text={`Fetch up to ${MAX_RESULTS_PER_CONNECTOR} ClinicalTrials.gov results per search.`} />
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => setTrialsMax((v) => bump(v, -5))}
                  >
                    −
                  </button>
                  <input
                    type="number"
                    min={1}
                    max={MAX_RESULTS_PER_CONNECTOR}
                    className={`${filterInputClass} w-full px-3 text-center`}
                    value={trialsMax}
                    onChange={(event) =>
                      setTrialsMax(
                        Math.max(
                          1,
                          Math.min(MAX_RESULTS_PER_CONNECTOR, Number(event.target.value) || 1)
                        )
                      )
                    }
                  />
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => setTrialsMax((v) => bump(v, 5))}
                  >
                    +
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <button
          type="button"
          disabled={searching || literatureConnectors.length === 0}
          onClick={() => void handleSearch()}
          className={primaryButton}
        >
          {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Search
        </button>
      </div>

      {showResults && (
        <div className={`${cardSurface} space-y-3 p-4`}>
          <div className="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              className={secondaryButton}
              onClick={() =>
                setSelectedKeys(new Set(selectableResults.map((item) => item.selection_key)))
              }
            >
              Select all with PDF
            </button>
            <button
              type="button"
              className={secondaryButton}
              onClick={() => setSelectedKeys(new Set())}
            >
              Clear selection
            </button>
          </div>

          <div className="max-h-[36rem] overflow-auto rounded-lg border border-line">
            <table className="min-w-[72rem] w-full border-collapse text-left text-sm">
              <thead className="sticky top-0 z-10 bg-soft text-xs font-semibold uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-3 py-2.5">Import</th>
                  <th className="px-3 py-2.5">Source</th>
                  <th className="px-3 py-2.5">PMID</th>
                  <th className="px-3 py-2.5">PMCID</th>
                  <th className="px-3 py-2.5">PubMed</th>
                  <th className="px-3 py-2.5">PMC</th>
                  <th className="px-3 py-2.5">NCT ID</th>
                  <th className="px-3 py-2.5">ClinicalTrials</th>
                  <th className="px-3 py-2.5">Published</th>
                  <th
                    className="px-3 py-2.5"
                    title="Title relevance to your search: full phrase match scores high even if the term appears late"
                  >
                    Accuracy
                  </th>
                  <th className="px-3 py-2.5">Title</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => {
                  const isPubmed = item.source === 'PubMed';
                  const disabled = isPubmed && !item.has_pmc_pdf;
                  const selected = selectedKeys.has(item.selection_key);
                  return (
                    <tr
                      key={item.selection_key}
                      className="border-t border-line"
                    >
                      <td className="px-3 py-2 align-middle">
                        <input
                          type="checkbox"
                          disabled={disabled}
                          checked={selected}
                          title={
                            disabled
                              ? 'No free PMC PDF available for this PubMed record'
                              : 'Import this publication'
                          }
                          onChange={() => toggleKey(item.selection_key, disabled)}
                        />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-fg">
                        {item.source}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-fg">
                        {isPubmed ? item.record_id : ''}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-fg">
                        {isPubmed ? item.pmcid || '—' : '—'}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <OpenLink href={pubmedUrl(item)} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <OpenLink href={pmcUrl(item)} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-fg">
                        {!isPubmed ? item.record_id : '—'}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <OpenLink href={clinicalTrialsUrl(item)} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-fg">
                        {formatPublishedDate(item.published_date)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <AccuracyBadge value={item.accuracy_pct ?? 0} />
                      </td>
                      <td className="max-w-lg px-3 py-2 text-fg">
                        {item.title || 'Untitled'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-sm text-muted">
            {selectedKeys.size} of {results.length} new publication(s) selected.
          </p>

          <button
            type="button"
            disabled={importing || selectedKeys.size === 0}
            onClick={() => void handleImport()}
            className={primaryButton}
          >
            {importing && <Loader2 className="h-4 w-4 animate-spin" />}
            Load selected documents
          </button>
        </div>
      )}

      {hasSearched && results.length === 0 && (
        <div className={`${cardSurface} p-4 text-sm text-warn-fg`}>
          {searchErrors.length > 0
            ? searchErrors.join(' · ')
            : 'No new publications found for this query.'}
        </div>
      )}
    </div>
  );
}
