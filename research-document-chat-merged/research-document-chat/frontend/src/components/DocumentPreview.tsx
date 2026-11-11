import { useState, useEffect, useMemo } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Download,
  History,
  PenLine,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  X,
  Loader2,
  Send,
  UploadCloud,
} from 'lucide-react';
import type { Artifact, DraftingAuditRecord, DraftingStats } from '../types/api';
import {
  getGeneratedDraftFile,
  getDraftSectionSourcesReport,
  uploadFilesToVeeva,
} from '../services/veevaDocumentService';
import { fetchDraftingAudit } from '../services/draftingAuditService';
import {
  commitDraftingEditorSession,
  fetchDraftingSectionReferences,
  startDraftingEditorSession,
  type DraftingSectionReference,
  type DraftingSectionReferences,
} from '../services/draftingEditorService';
import { getDocumentViewerBlob } from '../services/api';
import { API_BASE_URL } from '../config/api';
import { veevaButtonCompact } from '../config/pageTheme';
import VeevaMetadataForm, { validateVeevaFieldValues, type VeevaFieldValues } from './VeevaMetadataForm';
import type { VeevaUploadFieldSchema } from '../services/veevaDocumentService';
import ConfirmDialog from './ConfirmDialog';
import { useTheme } from '../contexts/ThemeContext';
import { useToast } from '../contexts/ToastContext';

interface DocumentPreviewProps {
  artifact: Artifact;
  /** When omitted, the change-request footer is hidden (e.g. repository pages). */
  onRequestChanges?: (feedback: string) => void;
  draftingStats?: DraftingStats;
  /** Fallback for audit lookup when message stats were not restored. */
  chatSessionId?: string | null;
  onClose: () => void;
  isStreaming?: boolean;
}

function formatDuration(seconds?: number): string {
  if (seconds == null || Number.isNaN(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

function formatTokens(n?: number): string {
  if (n == null || Number.isNaN(n)) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatApproxCostUsd(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return '—';
  if (n > 0 && n < 0.01) return `~$${n.toFixed(4)}`;
  return `~$${n.toFixed(2)}`;
}

function primaryAuditModel(record: DraftingAuditRecord | null): string {
  if (!record) return '—';
  if (record.model?.trim()) return record.model.trim();
  if (record.models?.length) return record.models.join(', ');
  const fromCalls = (record.llm_calls || [])
    .map((c) => String(c.model || '').trim())
    .filter(Boolean);
  if (!fromCalls.length) return '—';
  const counts = new Map<string, number>();
  for (const name of fromCalls) counts.set(name, (counts.get(name) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
}

function friendlyStepName(name: string): string {
  const map: Record<string, string> = {
    run_parallel_research: 'Research (template, regulatory, context)',
    write_sections_parallel: 'Write document sections',
    task: 'task',
    preview_document: 'Prepare draft preview',
    fill_document: 'Generate DOCX',
    generate_document: 'Generate document',
    select_template: 'Select template',
    retrieve_context: 'Retrieve repository context',
  };
  return map[name] || name.replace(/_/g, ' ');
}

const DRAFTING_STEP_ORDER = [
  'run_parallel_research',
  'write_sections_parallel',
  'task',
  'preview_document',
] as const;

function auditDraftingSteps(record: DraftingAuditRecord): Array<{
  step: string;
  label: string;
  duration_sec: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}> {
  if (record.drafting_steps?.length) {
    return record.drafting_steps.map((s) => ({
      step: s.step,
      label: s.label || friendlyStepName(s.step),
      duration_sec: s.duration_sec || 0,
      input_tokens: s.input_tokens || 0,
      output_tokens: s.output_tokens || 0,
      total_tokens: s.total_tokens || 0,
    }));
  }
  // Fallback for older audits without drafting_steps.
  const byTool = new Map<string, { duration_sec: number; total_tokens: number; input_tokens: number; output_tokens: number }>();
  for (const t of record.tool_timings || []) {
    if (!DRAFTING_STEP_ORDER.includes(t.tool as (typeof DRAFTING_STEP_ORDER)[number])) continue;
    if (byTool.has(t.tool)) continue;
    byTool.set(t.tool, {
      duration_sec: t.duration_sec || 0,
      total_tokens: t.total_tokens || 0,
      input_tokens: t.input_tokens || 0,
      output_tokens: t.output_tokens || 0,
    });
  }
  return DRAFTING_STEP_ORDER.filter((step) => byTool.has(step)).map((step) => {
    const row = byTool.get(step)!;
    return {
      step,
      label: friendlyStepName(step),
      duration_sec: row.duration_sec,
      input_tokens: row.input_tokens,
      output_tokens: row.output_tokens,
      total_tokens: row.total_tokens,
    };
  });
}

/**
 * Build a self-contained HTML page from mammoth-generated HTML content.
 * Splits content on .page-break markers into separate visual A4 pages.
 * Rendered inside a sandboxed iframe via Blob URL.
 */
function buildIframeDocument(htmlContent: string, title: string): string {
  // Normalize any remaining empty-paragraph breaks
  let normalized = htmlContent.replace(/<p>\s*<br\s*\/?>\s*<\/p>/gi, '<div class="page-break"></div>');

  const PAGE_BREAK = '<div class="page-break"></div>';
  const sections = normalized.split(PAGE_BREAK);
  const pages = sections
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => `<div class="page">${s}</div>`)
    .join('\n');

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title.replace(/</g, '&lt;')}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--border);
    font-family: Arial, 'Segoe UI', sans-serif;
    font-size: 10pt;
    line-height: 1.5;
    color: var(--fg);
    padding: 20px 12px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 20px;
  }

  /* A4 page simulation — responsive */
  .page {
    background: var(--card);
    width: min(210mm, 100%);
    min-height: auto;
    padding: 18mm clamp(8mm, 4vw, 22mm) 20mm clamp(8mm, 4vw, 18mm);
    box-shadow: var(--shadow-card);
    border-radius: 2px;
    position: relative;
    page-break-after: always;
  }

  /* Subtle page number at bottom */
  .page::after {
    content: counter(page-counter);
    counter-increment: page-counter;
    position: absolute;
    bottom: 10mm;
    right: 18mm;
    font-size: 9pt;
    color: var(--muted);
  }
  body { counter-reset: page-counter; }

  /* ── Typography (single ~1.5 line spacing, readable preview gaps) ── */
  h1 {
    font-size: 14pt;
    font-weight: 700;
    margin: 0 0 10pt;
    color: var(--fg);
    border-bottom: 1.5px solid var(--link);
    padding-bottom: 4pt;
    line-height: 1.35;
  }
  h2 {
    font-size: 12pt;
    font-weight: 700;
    margin: 14pt 0 8pt;
    color: var(--fg);
    border-bottom: 1px solid var(--border);
    padding-bottom: 3pt;
    line-height: 1.35;
  }
  h3 {
    font-size: 10pt;
    font-weight: 700;
    margin: 12pt 0 6pt;
    color: var(--fg);
    line-height: 1.4;
  }
  h4 {
    font-size: 10pt;
    font-weight: 600;
    margin: 10pt 0 4pt;
    color: var(--fg);
    line-height: 1.4;
  }
  p {
    margin: 0 0 8pt;
    text-align: justify;
    line-height: 1.5;
  }
  /* Keep true empty spacer paragraphs tiny, but do not crush normal breaks */
  p:empty {
    margin: 0 0 4pt;
    line-height: 1;
    min-height: 6pt;
  }
  p:has(> br:only-child) {
    margin: 0 0 4pt;
    line-height: 1;
    min-height: 6pt;
  }
  ul, ol {
    margin: 4pt 0 10pt 18pt;
    line-height: 1.5;
  }
  li {
    margin: 0 0 6pt;
    line-height: 1.5;
  }
  li:last-child { margin-bottom: 2pt; }
  strong, b { font-weight: 700; }
  em, i { font-style: italic; }

  /* ── TOC ── */
  .toc-heading { font-size: 13pt; font-weight: 700; margin: 10pt 0 7pt; }
  .toc-entry { margin: 1.5pt 0; font-size: 10pt; color: var(--fg); }
  .toc-entry a { color: var(--link); text-decoration: none; }
  .toc-entry a:hover { text-decoration: underline; }
  .toc2 { padding-left: 14pt; }
  .toc3 { padding-left: 28pt; }

  /* ── Tables ── */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 6pt 0 10pt;
    font-size: 9.5pt;
    line-height: 1.3;
    table-layout: fixed;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }

  th, td {
    border: 1px solid var(--muted);
    padding: 3pt 5pt;
    text-align: left;
    vertical-align: top;
    word-break: break-word;
  }

  /* Header rows — only style actual thead, not tr:first-child */
  thead.doc-header-row tr td,
  thead.doc-header-row tr th,
  thead tr td,
  thead tr th {
    background: var(--border);
    font-weight: 700;
    font-size: 9pt;
    color: var(--fg);
    border-bottom: 2px solid var(--muted);
  }

  tbody tr:nth-child(even) td {
    background: var(--soft);
  }

  /* SR NO. column */
  td[data-col="sr"], td:first-child.sr-col {
    text-align: center;
    white-space: nowrap;
    width: 36px !important;
  }

  /* ── Cover page / first table (document info) ── */
  .page:first-child > table:first-child {
    margin-top: 40pt;
    width: 80%;
    margin-left: auto;
    margin-right: auto;
  }
  .page:first-child > table:first-child td {
    border: 1px solid var(--muted);
    padding: 6pt 10pt;
  }

  /* ── Links ── */
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* ── Images ── */
  img { max-width: 100%; height: auto; display: block; }

  /* ── Print ── */
  @media print {
    body { background: none; padding: 0; gap: 0; }
    .page { box-shadow: none; border-radius: 0; margin: 0; width: 100%; }
    .page::after { display: none; }
  }

  /* ── Narrow panels ── */
  @media (max-width: 720px) {
    .page { padding: 10mm 8mm; }
    table { font-size: 8pt; }
    h1 { font-size: 13pt; }
    h2 { font-size: 11pt; }
  }
</style>
</head>
<body>
${pages}
</body>
</html>`;
}

const stripDraftExtension = (filename: string): string =>
  filename.replace(/\.(docx|doc|pdf|txt)$/i, '').trim();

export default function DocumentPreview({
  artifact,
  onRequestChanges,
  draftingStats,
  chatSessionId,
  onClose,
  isStreaming = false,
}: DocumentPreviewProps) {
  const [changeFeedback, setChangeFeedback] = useState('');
  const [iframeLoaded, setIframeLoaded] = useState(false);
  const [blobUrl, setBlobUrl] = useState('');
  const [showVeevaForm, setShowVeevaForm] = useState(false);
  const [veevaFields, setVeevaFields] = useState<VeevaFieldValues>({});
  const [veevaSchemaFields, setVeevaSchemaFields] = useState<VeevaUploadFieldSchema[]>([]);
  const [veevaError, setVeevaError] = useState('');
  const [veevaSuccess, setVeevaSuccess] = useState('');
  const [isUploadingToVeeva, setIsUploadingToVeeva] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [isDownloading, setIsDownloading] = useState(false);
  const [showReport, setShowReport] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState('');
  const [reportUrl, setReportUrl] = useState('');
  const [showAudit, setShowAudit] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState('');
  const [auditRecord, setAuditRecord] = useState<DraftingAuditRecord | null>(null);
  const [editorLoading, setEditorLoading] = useState(false);
  const [editorSaving, setEditorSaving] = useState(false);
  const [editorSessionId, setEditorSessionId] = useState('');
  const [editorWorkspaceUrl, setEditorWorkspaceUrl] = useState('');
  const [showCloseEditorConfirm, setShowCloseEditorConfirm] = useState(false);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [referenceError, setReferenceError] = useState('');
  const [referenceData, setReferenceData] = useState<DraftingSectionReferences | null>(null);
  const [activeReferenceIndex, setActiveReferenceIndex] = useState(0);
  const [referenceBlobUrl, setReferenceBlobUrl] = useState('');
  const [referenceViewerKind, setReferenceViewerKind] = useState('PDF');
  const [referencePage, setReferencePage] = useState(1);
  const toast = useToast();
  const { theme } = useTheme();

  const { data } = artifact;
  const downloadUrl = data.download_url || '';
  const filename = data.filename || '';
  const htmlContent = data.html_content || '';
  const docTypeLabel = String(data.doc_type || '').trim().toUpperCase();

  const veevaDocumentName = useMemo(
    () => stripDraftExtension(filename) || artifact.title.replace(/\s+—\s+Draft Preview$/i, '').trim(),
    [artifact.title, filename]
  );

  useEffect(() => {
    setVeevaFields({});
    setVeevaSchemaFields([]);
    setVeevaError('');
    setVeevaSuccess('');
    setDownloadError('');
    setShowVeevaForm(false);
  }, [artifact.artifact_id, artifact.title, filename]);

  // Create / revoke Blob URL inside useEffect so the lifecycle stays
  // in sync with React StrictMode's setup → cleanup → setup cycle.
  useEffect(() => {
    if (!htmlContent) {
      setBlobUrl('');
      return;
    }
    const doc = buildIframeDocument(htmlContent, artifact.title || 'Document Preview');
    const blob = new Blob([doc], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    setBlobUrl(url);
    setIframeLoaded(false);
    return () => URL.revokeObjectURL(url);
  }, [htmlContent, artifact.title]);

  const handleDownload = async () => {
    if (!downloadUrl || isDownloading) return;
    setIsDownloading(true);
    setDownloadError('');
    try {
      const fallbackFilename = filename || `${(artifact.title || 'generated_draft').replace(/[^\w.-]+/g, '_')}.docx`;
      const draftFile = await getGeneratedDraftFile(downloadUrl, fallbackFilename);
      const objectUrl = URL.createObjectURL(draftFile.blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = draftFile.filename || fallbackFilename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : 'Unable to download the generated draft.');
    } finally {
      setIsDownloading(false);
    }
  };

  const handleOpenAudit = async () => {
    setShowAudit(true);
    setAuditError('');
    setAuditLoading(true);
    try {
      const requestId = draftingStats?.request_id;
      const sessionId = draftingStats?.session_id || chatSessionId || undefined;
      if (!requestId && !sessionId) {
        throw new Error(
          'No audit record is linked to this draft yet. Open a draft from the same chat session, or generate a new draft to capture duration and tokens.'
        );
      }
      const record = await fetchDraftingAudit({
        requestId,
        sessionId,
      });
      setAuditRecord(record);
    } catch (error) {
      setAuditRecord(null);
      setAuditError(error instanceof Error ? error.message : 'Unable to load audit trail');
    } finally {
      setAuditLoading(false);
    }
  };

  const handleOpenReport = async () => {
    if (!downloadUrl || reportLoading) return;
    setShowReport(true);
    setReportError('');
    setReportLoading(true);
    try {
      const base = (filename || 'draft').replace(/\.(docx|doc)$/i, '');
      const reportFile = await getDraftSectionSourcesReport(
        downloadUrl,
        `${base}_section_sources.html`
      );
      if (reportUrl) URL.revokeObjectURL(reportUrl);
      const objectUrl = URL.createObjectURL(reportFile.blob);
      setReportUrl(objectUrl);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unable to load the section sources report.';
      setReportError(message);
      toast.error(message);
    } finally {
      setReportLoading(false);
    }
  };

  const handleOpenEditor = async () => {
    if (!filename || editorLoading) return;
    setEditorLoading(true);
    try {
      const session = await startDraftingEditorSession(filename);
      const base = API_BASE_URL.replace(/\/$/, '');
      const path = session.workspace_path.startsWith('/')
        ? session.workspace_path
        : `/${session.workspace_path}`;
      const url = new URL(`${base}${path}`, window.location.origin);
      url.searchParams.set('theme', theme === 'dark' ? 'dark' : 'light');
      url.searchParams.set('embedded', '1');
      url.searchParams.set('origin', 'drafting');
      setEditorSessionId(session.session_id);
      setEditorWorkspaceUrl(url.toString());
      toast.success(
        `Opened ${docTypeLabel || session.doc_type || 'draft'} interactive editor`
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unable to open drafting editor.';
      toast.error(message);
    } finally {
      setEditorLoading(false);
    }
  };

  const closeEditorWorkspace = () => {
    setEditorWorkspaceUrl('');
    setEditorSessionId('');
    setShowCloseEditorConfirm(false);
  };

  const loadReferenceDocument = async (
    reference: DraftingSectionReference,
    index: number
  ) => {
    setReferenceLoading(true);
    setReferenceError('');
    setActiveReferenceIndex(index);
    const usedPages = Array.from(
      new Set(
        (reference.page_numbers?.length
          ? reference.page_numbers
          : [reference.page_number]
        )
          .map((page) => Number(page))
          .filter((page) => Number.isFinite(page) && page >= 1)
      )
    ).sort((a, b) => a - b);
    setReferencePage(usedPages[0] || Math.max(1, Number(reference.page_number) || 1));
    try {
      if (!reference.file_id) {
        throw new Error(`Source file is not available in the repository: ${reference.name}`);
      }
      // Prefer DMS render (DOCX → PDF). Loading raw Office bytes in an iframe triggers Save As.
      const blob = await getDocumentViewerBlob(reference.file_id);
      const objectUrl = URL.createObjectURL(blob);
      const kind = blob.type.includes('pdf')
        ? 'PDF'
        : blob.type.startsWith('image/')
          ? 'IMG'
          : blob.type.includes('html')
            ? 'HTML'
            : (reference.file_type || 'FILE').toUpperCase();
      setReferenceViewerKind(kind);
      setReferenceBlobUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return objectUrl;
      });
    } catch (error) {
      setReferenceBlobUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return '';
      });
      setReferenceViewerKind('PDF');
      setReferenceError(
        error instanceof Error ? error.message : 'Unable to load the source document.'
      );
    } finally {
      setReferenceLoading(false);
    }
  };

  const openDraftingReference = async (sessionId: string, sectionNo: string) => {
    setReferenceOpen(true);
    setReferenceLoading(true);
    setReferenceError('');
    setReferenceData(null);
    try {
      const result = await fetchDraftingSectionReferences({ sessionId, sectionNo });
      const available = (result.references || []).filter((ref) => Boolean(ref.file_id));
      const filtered = { ...result, references: available };
      setReferenceData(filtered);
      if (!available.length) {
        throw new Error('No source documents for this section are available in the repository.');
      }
      await loadReferenceDocument(available[0], 0);
    } catch (error) {
      setReferenceError(
        error instanceof Error ? error.message : 'Unable to load section references.'
      );
      setReferenceLoading(false);
    }
  };

  const closeReferenceViewer = () => {
    setReferenceOpen(false);
    setReferenceData(null);
    setReferenceError('');
    setReferenceBlobUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return '';
    });
  };

  useEffect(() => {
    if (!editorWorkspaceUrl) return;
    const onMessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.source !== 'gxp-docs-ai-optimizer') return;
      if (data.type === 'drafting-editor-close') {
        closeEditorWorkspace();
      } else if (data.type === 'drafting-reference-open') {
        const sessionId = String(data.session_id || editorSessionId);
        const sectionNo = String(data.section_no || '');
        if (sessionId && sectionNo) {
          void openDraftingReference(sessionId, sectionNo);
        }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [editorWorkspaceUrl, editorSessionId]);

  const handleSaveAndCloseEditor = async () => {
    if (!editorSessionId || !filename || editorSaving) return;
    setEditorSaving(true);
    try {
      await commitDraftingEditorSession({
        sessionId: editorSessionId,
        filename,
      });
      toast.success('Edits saved to draft');
      closeEditorWorkspace();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unable to save editor changes.';
      toast.error(message);
    } finally {
      setEditorSaving(false);
    }
  };

  useEffect(() => {
    return () => {
      if (reportUrl) URL.revokeObjectURL(reportUrl);
    };
  }, [reportUrl]);

  useEffect(() => {
    return () => {
      if (referenceBlobUrl) URL.revokeObjectURL(referenceBlobUrl);
    };
  }, [referenceBlobUrl]);

  const validateVeevaFields = () => {
    if (!veevaSchemaFields.length) {
      return 'Veeva metadata is still loading. Please wait a moment.';
    }
    return validateVeevaFieldValues(veevaSchemaFields, veevaFields);
  };

  const handleUploadToVeeva = async () => {
    if (!downloadUrl) return;
    const validationError = validateVeevaFields();
    if (validationError) {
      setVeevaError(validationError);
      return;
    }

    setIsUploadingToVeeva(true);
    setVeevaError('');
    setVeevaSuccess('');
    try {
      const safeName = String(veevaFields.name__v || 'generated_sop').replace(/[^\w.-]+/g, '_');
      const fallbackFilename = filename || `${safeName}.docx`;
      const draftFile = await getGeneratedDraftFile(downloadUrl, fallbackFilename);
      const file = new File([draftFile.blob], draftFile.filename || fallbackFilename, {
        type: draftFile.contentType,
      });
      const metadata = Object.fromEntries(
        Object.entries(veevaFields).map(([key, value]) => [key, typeof value === 'string' ? value.trim() : value])
      );
      const uploadResult = await uploadFilesToVeeva([file], metadata);
      const count = uploadResult.count || uploadResult.results?.length || 1;
      setVeevaSuccess(`Uploaded ${count} draft document${count === 1 ? '' : 's'} to Veeva.`);
      setShowVeevaForm(false);
    } catch (error) {
      setVeevaError(error instanceof Error ? error.message : 'Veeva upload failed.');
    } finally {
      setIsUploadingToVeeva(false);
    }
  };

  const handleSubmitChanges = () => {
    if (!onRequestChanges || !changeFeedback.trim()) return;
    onRequestChanges(changeFeedback.trim());
    setChangeFeedback('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmitChanges();
    }
  };

  const activeReference = referenceData?.references[activeReferenceIndex] || null;
  const referenceUsedPages = (() => {
    const fromApi = (activeReference?.page_numbers || [])
      .map((page) => Number(page))
      .filter((page) => Number.isFinite(page) && page >= 1);
    const unique = Array.from(new Set(fromApi)).sort((a, b) => a - b);
    if (unique.length) return unique;
    const fallback = Number(activeReference?.page_number) || referencePage || 1;
    return [Math.max(1, fallback)];
  })();
  const referencePageIndex = Math.max(0, referenceUsedPages.indexOf(referencePage));
  const referenceViewerUrl = referenceBlobUrl
    ? `${referenceBlobUrl}#page=${referencePage}&zoom=page-width`
    : '';

  const goToUsedReferencePage = (direction: -1 | 1) => {
    if (!referenceUsedPages.length) return;
    const currentIdx =
      referencePageIndex >= 0
        ? referencePageIndex
        : referenceUsedPages.findIndex((page) => page >= referencePage);
    const idx = currentIdx >= 0 ? currentIdx : 0;
    const nextIdx = idx + direction;
    if (nextIdx < 0 || nextIdx >= referenceUsedPages.length) return;
    setReferencePage(referenceUsedPages[nextIdx]);
  };

  return (
    <div className="h-full flex flex-col bg-card border-l border-line">
      {/* Header */}
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-3 border-b border-line bg-soft">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-accent">
          <FileText className="w-4 h-4 text-onnavy" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-fg truncate flex items-center gap-2">
            {artifact.title || 'Document Preview'}
            {isStreaming && <Loader2 className="w-3 h-3 animate-spin text-brandcyan flex-shrink-0" />}
          </h3>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-xs text-muted">{data.doc_type}</span>
            {filename && (
              <>
                <span className="text-onnavymuted text-xs">•</span>
                <span className="text-xs text-muted truncate">{filename}</span>
              </>
            )}
          </div>
        </div>
        {!isStreaming && (
          <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-1.5">
            <button
              type="button"
              onClick={handleOpenAudit}
              className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1.5 text-[11px] font-medium text-fg shadow-sm transition-colors hover:bg-soft"
              title="Drafting audit trail (duration, tokens, LLM calls)"
            >
              <History className="h-3.5 w-3.5" />
              Audit trail
            </button>
            {downloadUrl && (
              <>
                {filename.toLowerCase().endsWith('.docx') && (
                  <button
                    type="button"
                    onClick={handleOpenEditor}
                    disabled={editorLoading}
                    className="flex items-center gap-1.5 rounded-lg border border-selb bg-selbg px-2.5 py-1.5 text-[11px] font-medium text-ok-fg shadow-sm transition-colors hover:bg-selbg"
                    title={`Open interactive editor for this ${docTypeLabel || 'draft'} template`}
                  >
                    {editorLoading ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <PenLine className="h-3.5 w-3.5" />
                    )}
                    Editor
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleOpenReport}
                  disabled={reportLoading}
                  className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1.5 text-[11px] font-medium text-fg shadow-sm transition-colors hover:bg-soft"
                  title="View section sources report (Level 1 / Level 2), including GQS"
                >
                  {reportLoading ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <FileText className="w-3.5 h-3.5" />
                  )}
                  Report
                </button>
                <button
                  onClick={handleDownload}
                  disabled={isDownloading}
                  className="flex items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1.5 text-[11px] font-medium text-accentfg shadow-sm transition-colors hover:bg-accent"
                  title="Download document"
                >
                  {isDownloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                  {isDownloading ? 'Downloading...' : 'Download'}
                </button>
                <button
                  onClick={() => {
                    setShowVeevaForm(true);
                    setVeevaError('');
                    setVeevaSuccess('');
                  }}
                  className={veevaButtonCompact}
                  title="Upload generated draft to Veeva"
                >
                  <UploadCloud className="w-3.5 h-3.5" />
                  Upload to Veeva
                </button>
              </>
            )}
          </div>
        )}
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-line transition-colors text-muted hover:text-fg flex-shrink-0"
          title="Close"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {editorWorkspaceUrl && (
        <div className="fixed inset-0 z-[110] flex flex-col bg-card">
          <div className="flex h-14 flex-shrink-0 items-center gap-3 border-b border-line px-4">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-accent">
              <PenLine className="h-4 w-4 text-onnavy" />
            </div>
            <div className="flex min-w-0 flex-1 flex-col justify-center">
              <h4 className="truncate text-sm font-semibold leading-5 text-fg">
                {docTypeLabel || 'Draft'} interactive editor
              </h4>
              <p className="truncate text-xs leading-4 text-muted">
                Editing the drafted {docTypeLabel || 'document'} template — separate from Document Optimizer
              </p>
            </div>
            <button
              type="button"
              onClick={handleSaveAndCloseEditor}
              disabled={editorSaving}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[11px] font-medium text-accentfg shadow-sm transition-colors hover:bg-accent disabled:opacity-60"
            >
              {editorSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Save to draft
            </button>
            <button
              type="button"
              onClick={() => setShowCloseEditorConfirm(true)}
              disabled={editorSaving}
              className="rounded-lg border border-line bg-card px-3 py-1.5 text-[11px] font-medium text-fg shadow-sm hover:bg-soft"
            >
              Close
            </button>
          </div>
          <iframe
            src={editorWorkspaceUrl}
            title={`${docTypeLabel || 'Draft'} interactive editor`}
            className="min-h-0 w-full flex-1 border-0 bg-card"
            allow="clipboard-read; clipboard-write"
          />
        </div>
      )}

      {referenceOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-scrim p-5">
          <div className="flex h-[92vh] w-full max-w-6xl flex-col rounded-2xl border border-line bg-soft shadow-2xl">
            <div className="flex h-14 flex-shrink-0 items-center gap-3 rounded-t-2xl border-b border-line bg-card px-4">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-selbg text-ok-fg">
                  <FileText className="h-4 w-4" />
                </div>
                <div className="flex min-w-0 flex-1 flex-col justify-center">
                  <h4
                    className="truncate text-sm font-semibold leading-5 text-fg"
                    title={activeReference?.name || 'Section reference'}
                  >
                    {activeReference?.name || 'Section reference'}
                  </h4>
                  <p
                    className="truncate text-xs leading-4 text-link"
                    title={`Target: ${referenceData?.section_no || '—'} ${referenceData?.section_title || ''}`.trim()}
                  >
                    Target: {referenceData?.section_no || '—'} {referenceData?.section_title || ''}
                  </p>
                </div>
              </div>

              <div className="flex flex-shrink-0 items-center gap-2">
                <span className="inline-flex h-8 items-center rounded-full border border-selb bg-selbg px-2.5 text-xs font-semibold text-ok-fg">
                  ✓ Verified original
                </span>

                <div className="inline-flex h-8 items-center rounded-lg border border-selb bg-selbg">
                  <button
                    type="button"
                    onClick={() => {
                      const index = Math.max(0, activeReferenceIndex - 1);
                      const next = referenceData?.references[index];
                      if (next) void loadReferenceDocument(next, index);
                    }}
                    className="inline-flex h-full items-center px-2 text-ok-fg hover:bg-selbg disabled:opacity-40"
                    disabled={!referenceData?.references.length || activeReferenceIndex <= 0}
                    title="Previous source document"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <span className="inline-flex h-full min-w-[4.75rem] items-center justify-center border-x border-selb px-2 text-xs font-semibold text-link">
                    Doc {referenceData?.references.length ? activeReferenceIndex + 1 : 0}/
                    {referenceData?.references.length || 0}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      const last = (referenceData?.references.length || 1) - 1;
                      const index = Math.min(last, activeReferenceIndex + 1);
                      const next = referenceData?.references[index];
                      if (next) void loadReferenceDocument(next, index);
                    }}
                    className="inline-flex h-full items-center px-2 text-ok-fg hover:bg-selbg disabled:opacity-40"
                    disabled={
                      !referenceData?.references.length ||
                      activeReferenceIndex >= (referenceData?.references.length || 0) - 1
                    }
                    title="Next source document"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>

                <div className="inline-flex h-8 items-center rounded-lg border border-line bg-card">
                  <button
                    type="button"
                    onClick={() => goToUsedReferencePage(-1)}
                    className="inline-flex h-full items-center px-2 text-muted hover:bg-soft disabled:opacity-40"
                    disabled={referencePageIndex <= 0}
                    title="Previous used source page"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <span
                    className="inline-flex h-full min-w-[4.5rem] items-center justify-center border-x border-line px-2 text-xs font-semibold text-fg"
                    title={
                      referenceUsedPages.length > 1
                        ? `Used pages: ${referenceUsedPages.join(', ')}`
                        : `Used page: ${referencePage}`
                    }
                  >
                    Page {referencePage}
                    {referenceUsedPages.length > 1
                      ? ` · ${referencePageIndex + 1}/${referenceUsedPages.length}`
                      : ''}
                  </span>
                  <button
                    type="button"
                    onClick={() => goToUsedReferencePage(1)}
                    className="inline-flex h-full items-center px-2 text-muted hover:bg-soft disabled:opacity-40"
                    disabled={
                      referencePageIndex < 0 ||
                      referencePageIndex >= referenceUsedPages.length - 1
                    }
                    title="Next used source page"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>

                <span className="inline-flex h-8 items-center rounded-lg bg-line px-2.5 text-xs font-semibold text-fg">
                  {referenceViewerKind}
                </span>
                {referenceBlobUrl && activeReference && (
                  <>
                    <a
                      href={referenceBlobUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-soft"
                      title="Open preview in a new tab"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </a>
                    <a
                      href={referenceBlobUrl}
                      download={`${activeReference.name.replace(/\.[^.]+$/, '') || 'reference'}-preview.pdf`}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-soft"
                      title="Download preview"
                    >
                      <Download className="h-4 w-4" />
                    </a>
                  </>
                )}
                <button
                  type="button"
                  onClick={closeReferenceViewer}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-soft"
                  title="Close reference"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="relative min-h-0 flex-1 overflow-hidden rounded-b-2xl bg-line p-5">
              {referenceLoading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-soft">
                  <div className="flex items-center gap-2 text-sm text-muted">
                    <Loader2 className="h-5 w-5 animate-spin" />
                    Loading exact source page…
                  </div>
                </div>
              )}
              {!referenceLoading && referenceError && (
                <div className="mx-auto mt-16 max-w-xl rounded-xl border border-err-line bg-card p-5 text-sm text-err-fg shadow">
                  {referenceError}
                </div>
              )}
              {!referenceError && referenceViewerUrl && (
                <iframe
                  key={referenceViewerUrl}
                  src={referenceViewerUrl}
                  title={`Reference: ${activeReference?.name || 'source document'}`}
                  className="h-full w-full rounded-lg border-0 bg-card shadow-xl"
                />
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={showCloseEditorConfirm}
        title="Close editor?"
        message="Unsaved edits in the interactive editor will be discarded unless you save to draft first."
        confirmText="Close without saving"
        cancelText="Keep editing"
        confirmVariant="danger"
        onCancel={() => setShowCloseEditorConfirm(false)}
        onConfirm={closeEditorWorkspace}
      />

      {showAudit && (
        <div className="fixed inset-0 z-[115] flex items-center justify-center bg-scrim p-4">
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-line bg-card shadow-xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div>
                <h4 className="text-sm font-semibold text-fg">Drafting audit trail</h4>
                <p className="mt-0.5 text-xs text-muted">
                  Overall usage, model, and per-step tokens / time
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowAudit(false)}
                className="rounded-lg p-1.5 text-muted hover:bg-soft"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs text-fg">
              {auditLoading && (
                <div className="flex items-center gap-2 py-8 justify-center text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading audit trail…
                </div>
              )}
              {!auditLoading && auditError && (
                <div className="rounded-lg border border-err-line bg-err-bg px-3 py-2 text-err-fg">
                  {auditError}
                </div>
              )}
              {!auditLoading && !auditError && auditRecord && (
                <div className="space-y-4">
                  <div>
                    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
                      Overall
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                      <div className="rounded-lg border border-line bg-soft px-3 py-2 sm:col-span-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">Model</div>
                        <div className="mt-0.5 truncate text-sm font-semibold" title={primaryAuditModel(auditRecord)}>
                          {primaryAuditModel(auditRecord)}
                        </div>
                      </div>
                      <div className="rounded-lg border border-line bg-soft px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">Duration</div>
                        <div className="mt-0.5 text-sm font-semibold">
                          {formatDuration(auditRecord.duration_sec)}
                        </div>
                      </div>
                      <div className="rounded-lg border border-line bg-soft px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">Total tokens</div>
                        <div className="mt-0.5 text-sm font-semibold">
                          {formatTokens(auditRecord.llm_usage?.total_tokens)}
                        </div>
                      </div>
                      <div className="rounded-lg border border-line bg-soft px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">Approx cost</div>
                        <div
                          className="mt-0.5 text-sm font-semibold"
                          title={
                            auditRecord.cost_estimate
                              ? `Approx from list rates: $${auditRecord.cost_estimate.input_usd_per_1m}/1M in · $${auditRecord.cost_estimate.output_usd_per_1m}/1M out`
                              : 'Approximate token cost'
                          }
                        >
                          {formatApproxCostUsd(
                            auditRecord.approx_cost_usd ?? auditRecord.cost_estimate?.approx_cost_usd
                          )}
                        </div>
                      </div>
                      <div className="rounded-lg border border-line bg-soft px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">Status</div>
                        <div className="mt-0.5 text-sm font-semibold capitalize">
                          {auditRecord.status || '—'}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
                      <div className="rounded-lg border border-line px-3 py-2">
                        <span className="text-muted">Input tokens </span>
                        <span className="font-semibold">
                          {formatTokens(auditRecord.llm_usage?.input_tokens)}
                        </span>
                      </div>
                      <div className="rounded-lg border border-line px-3 py-2">
                        <span className="text-muted">Output tokens </span>
                        <span className="font-semibold">
                          {formatTokens(auditRecord.llm_usage?.output_tokens)}
                        </span>
                      </div>
                      <div className="rounded-lg border border-line px-3 py-2">
                        <span className="text-muted">LLM calls </span>
                        <span className="font-semibold">
                          {auditRecord.llm_usage?.llm_call_count ?? auditRecord.llm_calls?.length ?? 0}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div>
                    <div className="mb-1.5 font-semibold text-fg">
                      Drafting steps (time + tokens)
                    </div>
                    <div className="space-y-1.5">
                      {(() => {
                        const steps = auditDraftingSteps(auditRecord);
                        if (!steps.length) {
                          return <p className="text-muted">No drafting step timings recorded.</p>;
                        }
                        return steps.map((step) => (
                          <div
                            key={step.step}
                            className="rounded border border-line px-3 py-2"
                          >
                            <div className="text-sm font-medium text-fg">
                              {step.label}
                            </div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted">
                              <span>{formatDuration(step.duration_sec)}</span>
                              <span>{formatTokens(step.total_tokens)} tok</span>
                              <span>
                                in {formatTokens(step.input_tokens)} / out{' '}
                                {formatTokens(step.output_tokens)}
                              </span>
                            </div>
                          </div>
                        ));
                      })()}
                    </div>
                  </div>

                  <div>
                    <div className="mb-1.5 font-semibold text-fg">
                      DMS retrievals
                    </div>
                    <div className="space-y-1">
                      {(auditRecord.dms_retrievals || []).length === 0 && (
                        <p className="text-muted">No DMS retrievals recorded.</p>
                      )}
                      {(auditRecord.dms_retrievals || []).map((r, idx) => (
                        <div
                          key={`${r.agent}-${idx}`}
                          className="rounded border border-line px-2 py-1.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium">{r.agent}</span>
                            <span className="text-muted">
                              {r.chunks_retrieved} chunks · {formatDuration(r.duration_sec)}
                            </span>
                          </div>
                          {!!r.queries?.length && (
                            <p className="mt-0.5 truncate text-[11px] text-muted">
                              {r.queries.slice(0, 2).join(' · ')}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showReport && (
        <div className="fixed inset-0 z-[115] flex items-center justify-center bg-scrim p-4">
          <div className="flex h-[85vh] w-full max-w-4xl flex-col rounded-xl border border-line bg-card shadow-xl">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div>
                <h4 className="text-sm font-semibold text-fg">
                  Section sources report
                </h4>
                <p className="mt-0.5 text-xs text-muted">
                  Level 1 / Level 2 sources used while drafting (including GQS)
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowReport(false)}
                className="rounded-lg p-1.5 text-muted hover:bg-soft"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="relative min-h-0 flex-1 bg-soft">
              {reportLoading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 text-sm text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading report…
                </div>
              )}
              {!reportLoading && reportError && (
                <div className="m-4 rounded-lg border border-err-line bg-err-bg px-3 py-2 text-xs text-err-fg">
                  {reportError}
                </div>
              )}
              {!reportLoading && !reportError && reportUrl && (
                <iframe
                  src={reportUrl}
                  title="Section sources report"
                  className="h-full w-full border-0 bg-card"
                  sandbox="allow-same-origin"
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Artifact content — iframe sandbox */}
      {veevaSuccess && (
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-selb bg-selbg px-4 py-2 text-xs font-semibold text-ok-fg">
          <CheckCircle2 className="h-4 w-4" />
          {veevaSuccess}
        </div>
      )}

      {downloadError && (
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-err-line bg-err-bg px-4 py-2 text-xs font-semibold text-err-fg">
          <AlertCircle className="h-4 w-4" />
          {downloadError}
        </div>
      )}

      {showVeevaForm && (
        <div className="flex-shrink-0 border-b border-line bg-card px-4 py-4">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h4 className="text-sm font-semibold text-fg">Veeva metadata</h4>
              <p className="mt-0.5 text-xs text-muted">
                Select document name, type, and subtype. Remaining metadata is resolved from Veeva automatically.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowVeevaForm(false)}
              disabled={isUploadingToVeeva}
              className="rounded-lg p-1.5 text-muted transition-colors hover:bg-soft hover:text-fg disabled:opacity-60"
              title="Close Veeva metadata"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {veevaError && (
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-err-line bg-err-bg px-3 py-2 text-xs font-semibold text-err-fg">
              <AlertCircle className="h-4 w-4 flex-shrink-0" />
              {veevaError}
            </div>
          )}

          <VeevaMetadataForm
            documentName={veevaDocumentName}
            values={veevaFields}
            onSchemaLoaded={setVeevaSchemaFields}
            onChange={(next) => {
              setVeevaFields(next);
              setVeevaError('');
              setVeevaSuccess('');
            }}
            disabled={isUploadingToVeeva}
            compact
          />

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowVeevaForm(false)}
              disabled={isUploadingToVeeva}
              className="rounded-lg border border-line px-4 py-2 text-sm font-semibold text-fg transition-colors hover:bg-soft disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleUploadToVeeva}
              disabled={isUploadingToVeeva}
              className={`${veevaButtonCompact} px-4 py-2 text-sm disabled:cursor-not-allowed disabled:bg-line disabled:text-muted`}
            >
              {isUploadingToVeeva ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
              Upload to Veeva
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-hidden bg-line relative">
        {isStreaming ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <Loader2 className="w-8 h-8 animate-spin text-brandcyan" />
            <span className="text-sm text-muted">Generating document...</span>
          </div>
        ) : !blobUrl ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center">
            <Loader2 className="w-6 h-6 animate-spin text-brandcyan" />
            <span className="text-sm text-muted">Preparing preview...</span>
          </div>
        ) : (
          <>
            {!iframeLoaded && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-line z-10">
                <Loader2 className="w-6 h-6 animate-spin text-brandcyan" />
                <span className="text-sm text-muted">Rendering document...</span>
              </div>
            )}
            <iframe
              src={blobUrl}
              sandbox="allow-scripts allow-same-origin"
              className="w-full h-full border-0"
              title="Document Preview"
              onLoad={() => setIframeLoaded(true)}
            />
          </>
        )}
      </div>

      {/* Inline edit bar — chat draft preview only */}
      {!isStreaming && onRequestChanges && (
        <div className="flex-shrink-0 px-4 py-3 border-t border-line bg-soft">
          <div className="flex items-end gap-2">
            <textarea
              value={changeFeedback}
              onChange={(e) => {
                setChangeFeedback(e.target.value);
                e.target.style.height = 'auto';
                e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
              }}
              onKeyDown={handleKeyDown}
              placeholder="Request changes to the document..."
              rows={1}
              className="flex-1 px-3 py-2 text-sm border border-line rounded-lg bg-card text-fg placeholder-muted focus:ring-2 focus:ring-selb focus:border-transparent outline-none resize-none overflow-hidden"
            />
            <button
              onClick={handleSubmitChanges}
              disabled={!changeFeedback.trim()}
              className="flex-shrink-0 rounded-lg bg-accent p-2 text-accentfg shadow-sm transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:bg-line"
              title="Send feedback"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
