import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, FileText, RefreshCw, Upload, X, Loader2 } from 'lucide-react';
import {
  listRepositoryDocuments,
  type Repository,
  type RepositoryDocument,
} from '../services/repositoryService';
import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import {
  createTherapeuticAreaFolder,
  deleteTherapeuticAreaFolder,
  getFolderDocumentCounts,
  listTherapeuticAreas,
  renameTherapeuticAreaFolder,
  uploadTherapeuticAreaDocuments,
} from '../services/digitalMslService';
import { RepoTreePanel, type RepoFolder } from './RepoTreePanel';
import { useToast } from '../contexts/ToastContext';
import { filterInputClass, panelHeader, primaryButton, secondaryButton } from '../config/pageTheme';

interface DocumentsPanelProps {
  repositoryId: string;
  repositoryName?: string;
  repositories?: Repository[];
  selectedRepoId?: string;
  onSelectRepo?: (id: string) => void;
  hideRepoSelector?: boolean;
}

const STATUS_STYLES: Record<string, string> = {
  completed: 'bg-selbg text-ok-fg border-selb',
  indexed: 'bg-selbg text-ok-fg border-selb',
  processing: 'bg-selbg text-ok-fg border-selb',
  uploaded: 'bg-warn-bg text-warn-fg border-warn-line',
  failed: 'bg-err-bg text-err-fg border-err-line',
};

function displayDocStatus(status?: string): string {
  const s = (status || '').toLowerCase();
  if (s === 'completed') return 'Indexed';
  if (s === 'processing' || s === 'in_progress') return 'Processing';
  if (s === 'failed') return 'Failed';
  if (!s || s === 'pending') return 'Uploaded';
  return status || 'Uploaded';
}

function formatDate(ts?: number): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
}

export const DocumentsPanel: React.FC<DocumentsPanelProps> = ({
  repositoryId,
  repositoryName,
  repositories,
  selectedRepoId,
  onSelectRepo,
  hideRepoSelector = false,
}) => {
  const toast = useToast();
  const [documents, setDocuments] = useState<RepositoryDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<RepositoryDocument | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [folders, setFolders] = useState<RepoFolder[]>([]);
  const [selectedFolderId, setSelectedFolderId] = useState('');
  const [folderName, setFolderName] = useState('');
  const [parentFolderId, setParentFolderId] = useState('');
  const [docCounts, setDocCounts] = useState<Record<string, number>>({});
  const [foldersOpen, setFoldersOpen] = useState(false);
  const [docSearch, setDocSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDocuments(await listRepositoryDocuments(repositoryId));
      const areas = await listTherapeuticAreas().catch(() => []);
      const match = areas.find((area) => area.repository_id === repositoryId);
      setFolders(match?.folders || []);
      const counts = await getFolderDocumentCounts(repositoryId).catch(() => ({}));
      setDocCounts(counts);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not load documents.');
    } finally {
      setLoading(false);
    }
  }, [repositoryId, toast]);

  useEffect(() => {
    setSelected(null);
    load();
  }, [load]);

  const onUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (files.length === 0) return;
    setUploading(true);
    try {
      await uploadTherapeuticAreaDocuments(repositoryId, files, selectedFolderId);
      toast.success(`Uploaded ${files.length} file(s).`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setUploading(false);
    }
  };

  const renderUrl = selected ? `${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_RENDER(selected.document_id)}` : '';

  const filteredDocs = documents.filter((doc) => {
    const q = docSearch.trim().toLowerCase();
    if (!q) return true;
    return (doc.original_file_name || '').toLowerCase().includes(q);
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
      <div className="lg:col-span-5 xl:col-span-4 flex flex-col rounded-lg border border-line bg-card overflow-hidden min-h-[480px]">
        <div className={`${panelHeader} flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between`}>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-fg">Medical documents</h2>
            {onSelectRepo && repositories && !hideRepoSelector ? (
              <select
                value={selectedRepoId || repositoryId}
                onChange={(e) => onSelectRepo(e.target.value)}
                className={`mt-1.5 ${filterInputClass} text-xs max-w-full`}
              >
                {repositories.map((r) => (
                  <option key={r.repository_id} value={r.repository_id}>
                    {r.name || r.repository_id}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-xs text-muted mt-0.5">
                {repositoryName || repositoryId} · {documents.length} file(s)
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" onClick={load} className={secondaryButton} title="Refresh">
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button type="button" onClick={() => fileInput.current?.click()} disabled={uploading} className={primaryButton}>
              {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              Upload
            </button>
            <input ref={fileInput} type="file" multiple accept=".pdf,.docx,.doc,.txt" className="hidden" onChange={onUpload} />
          </div>
        </div>

        <div className="px-3 py-2 border-b border-line">
          <input
            value={docSearch}
            onChange={(e) => setDocSearch(e.target.value)}
            placeholder="Filter documents…"
            className={`${filterInputClass} w-full text-xs`}
          />
        </div>

        <button
          type="button"
          className="flex items-center gap-2 px-4 py-2 text-xs font-medium text-muted border-b border-line hover:bg-soft"
          onClick={() => setFoldersOpen((v) => !v)}
        >
          {foldersOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          Repo folders {selectedFolderId ? '(folder selected for upload)' : ''}
        </button>
        {foldersOpen && (
          <div className="px-3 py-3 border-b border-line max-h-48 overflow-y-auto">
            <RepoTreePanel
              folders={folders}
              selectedFolderId={selectedFolderId}
              documentCounts={docCounts}
              onSelect={setSelectedFolderId}
              newFolderName={folderName}
              onNewFolderNameChange={setFolderName}
              parentFolderId={parentFolderId}
              loading={loading}
              onCreate={async (name, parentId) => {
                const created = await createTherapeuticAreaFolder(repositoryId, name, parentId);
                setFolders((prev) => [...prev, created as RepoFolder]);
                setFolderName('');
                toast.success('Folder created.');
              }}
              onRename={async (folderId, name) => {
                await renameTherapeuticAreaFolder(folderId, name);
                setFolders((prev) => prev.map((f) => (f.folder_id === folderId ? { ...f, name } : f)));
              }}
              onDelete={async (folderId) => {
                await deleteTherapeuticAreaFolder(folderId);
                setFolders((prev) => prev.filter((f) => f.folder_id !== folderId));
                if (selectedFolderId === folderId) setSelectedFolderId('');
                await load();
              }}
            />
          </div>
        )}

        <div className="grid grid-cols-[1fr_auto_auto] gap-2 px-4 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted border-b border-line bg-soft">
          <span>Document</span>
          <span className="w-20 text-right">Status</span>
          <span className="w-28 text-right hidden sm:block">Uploaded</span>
        </div>
        <ul className="flex-1 overflow-y-auto divide-y divide-line">
          {filteredDocs.map((doc) => (
            <li key={doc.document_id}>
              <button
                type="button"
                onClick={() => setSelected(doc)}
                className={`w-full text-left px-4 py-2.5 grid grid-cols-[1fr_auto_auto] gap-2 items-center hover:bg-soft transition ${
                  selected?.document_id === doc.document_id ? 'bg-selbg' : ''
                }`}
              >
                <span className="flex items-center gap-2 min-w-0">
                  <FileText className="w-3.5 h-3.5 text-muted shrink-0" />
                  <span className="text-xs font-medium text-fg truncate">{doc.original_file_name}</span>
                </span>
                <span className={`w-20 text-right text-[10px] px-1.5 py-0.5 rounded border ${STATUS_STYLES[doc.status] || STATUS_STYLES.uploaded}`}>
                  {displayDocStatus(doc.status)}
                </span>
                <span className="w-28 text-right text-[10px] text-muted hidden sm:block">{formatDate(doc.upload_timestamp)}</span>
              </button>
            </li>
          ))}
          {!loading && filteredDocs.length === 0 && (
            <li className="px-4 py-12 text-center text-xs text-muted">No documents match. Import literature or upload files.</li>
          )}
        </ul>
      </div>

      <div className="lg:col-span-7 xl:col-span-8 rounded-lg border border-line bg-card overflow-hidden flex flex-col min-h-[480px]">
        {selected ? (
          <>
            <div className={`${panelHeader} flex items-start justify-between gap-3`}>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-fg truncate">{selected.original_file_name}</h3>
                <p className="text-xs text-muted mt-0.5">Document preview</p>
              </div>
              <button type="button" onClick={() => setSelected(null)} className="p-1.5 text-muted hover:bg-soft rounded-lg" aria-label="Close preview">
                <X className="w-4 h-4" />
              </button>
            </div>
            <iframe title="Document preview" src={renderUrl} className="flex-1 w-full min-h-[420px] bg-soft border-0" />
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center text-muted p-8">
            <FileText className="w-10 h-10 mb-3 stroke-1 opacity-40" />
            <p className="text-sm font-medium text-muted">Select a document</p>
            <p className="text-xs mt-1 max-w-xs">Preview opens here. Passages and chunk text are not shown in this view.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default DocumentsPanel;
