import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, Pencil, Trash2 } from 'lucide-react';

export type RepoFolder = {
  folder_id: string;
  name: string;
  parent_folder_id?: string;
  sort_order?: number;
};

interface RepoTreePanelProps {
  folders: RepoFolder[];
  selectedFolderId?: string;
  documentCounts?: Record<string, number>;
  onSelect?: (folderId: string) => void;
  onCreate?: (name: string, parentFolderId: string) => Promise<void> | void;
  onRename?: (folderId: string, name: string) => Promise<void> | void;
  onDelete?: (folderId: string) => Promise<void> | void;
  newFolderName?: string;
  onNewFolderNameChange?: (value: string) => void;
  parentFolderId?: string;
  loading?: boolean;
  error?: string;
}

function buildTree(folders: RepoFolder[]) {
  const byParent: Record<string, RepoFolder[]> = {};
  const byId: Record<string, RepoFolder> = {};
  for (const folder of folders) {
    byId[folder.folder_id] = folder;
    const key = folder.parent_folder_id || '';
    if (!byParent[key]) byParent[key] = [];
    byParent[key].push(folder);
  }
  for (const key of Object.keys(byParent)) {
    byParent[key].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name));
  }
  return { byParent, byId };
}

function breadcrumbTrail(folderId: string, byId: Record<string, RepoFolder>): RepoFolder[] {
  const trail: RepoFolder[] = [];
  let current = byId[folderId];
  const guard = new Set<string>();
  while (current && !guard.has(current.folder_id)) {
    guard.add(current.folder_id);
    trail.unshift(current);
    const parentId = current.parent_folder_id || '';
    current = parentId ? byId[parentId] : undefined;
  }
  return trail;
}

function childFolderCount(folderId: string, folders: RepoFolder[]): number {
  return folders.filter((f) => (f.parent_folder_id || '') === folderId).length;
}

function TreeNode({
  folder,
  depth,
  byParent,
  selectedFolderId,
  documentCounts,
  onSelect,
  onRenameClick,
  onDeleteClick,
}: {
  folder: RepoFolder;
  depth: number;
  byParent: Record<string, RepoFolder[]>;
  selectedFolderId?: string;
  documentCounts?: Record<string, number>;
  onSelect?: (id: string) => void;
  onRenameClick?: (folder: RepoFolder) => void;
  onDeleteClick?: (folder: RepoFolder) => void;
}) {
  const children = byParent[folder.folder_id] || [];
  const [open, setOpen] = React.useState(true);
  const selected = selectedFolderId === folder.folder_id;
  const count = documentCounts?.[folder.folder_id] || 0;
  return (
    <li>
      <div
        className={`flex items-center gap-0.5 rounded ${selected ? 'bg-selbg' : 'hover:bg-soft'}`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {children.length ? (
          <button type="button" onClick={() => setOpen((v) => !v)} className="p-0.5 shrink-0">
            {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        <button type="button" onClick={() => onSelect?.(folder.folder_id)} className="flex-1 flex items-center gap-1 text-left text-xs py-1 min-w-0">
          <Folder className="w-3.5 h-3.5 shrink-0" />
          <span className="truncate">{folder.name}</span>
          {count > 0 && <span className="text-[10px] text-muted shrink-0">({count})</span>}
        </button>
        {onRenameClick && (
          <button type="button" className="p-1 text-muted hover:text-link" onClick={() => onRenameClick(folder)} title="Rename">
            <Pencil className="w-3 h-3" />
          </button>
        )}
        {onDeleteClick && (
          <button type="button" className="p-1 text-muted hover:text-err-fg" onClick={() => onDeleteClick(folder)} title="Delete">
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>
      {open && children.length > 0 && (
        <ul>
          {children.map((child) => (
            <TreeNode
              key={child.folder_id}
              folder={child}
              depth={depth + 1}
              byParent={byParent}
              selectedFolderId={selectedFolderId}
              documentCounts={documentCounts}
              onSelect={onSelect}
              onRenameClick={onRenameClick}
              onDeleteClick={onDeleteClick}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function RepoTreePanel({
  folders,
  selectedFolderId = '',
  documentCounts,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  newFolderName = '',
  onNewFolderNameChange,
  parentFolderId = '',
  loading = false,
  error = '',
}: RepoTreePanelProps) {
  const { byParent, byId } = useMemo(() => buildTree(folders), [folders]);
  const roots = byParent[''] || [];
  const crumbs = selectedFolderId ? breadcrumbTrail(selectedFolderId, byId) : [];
  const [renameTarget, setRenameTarget] = useState<RepoFolder | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RepoFolder | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);

  const submitRename = async () => {
    if (!renameTarget || !onRename) return;
    const next = renameValue.trim();
    if (!next) return;
    if (next === renameTarget.name) {
      setRenameTarget(null);
      return;
    }
    setRenameBusy(true);
    try {
      await onRename(renameTarget.folder_id, next);
      setRenameTarget(null);
    } finally {
      setRenameBusy(false);
    }
  };

  const submitDelete = async () => {
    if (!deleteTarget || !onDelete) return;
    setDeleteBusy(true);
    try {
      await onDelete(deleteTarget.folder_id);
      if (selectedFolderId === deleteTarget.folder_id) onSelect?.('');
      setDeleteTarget(null);
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-card p-3 space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Repo folders</p>
      <nav className="flex flex-wrap items-center gap-1 text-[11px] text-muted" aria-label="Folder breadcrumb">
        <button type="button" className={`px-1.5 py-0.5 rounded ${!selectedFolderId ? 'bg-selbg text-ok-fg' : 'hover:underline'}`} onClick={() => onSelect?.('')}>
          Root
        </button>
        {crumbs.map((crumb) => (
          <React.Fragment key={crumb.folder_id}>
            <span>/</span>
            <button
              type="button"
              className={`px-1.5 py-0.5 rounded truncate max-w-[8rem] ${selectedFolderId === crumb.folder_id ? 'bg-selbg text-ok-fg' : 'hover:underline'}`}
              onClick={() => onSelect?.(crumb.folder_id)}
            >
              {crumb.name}
            </button>
          </React.Fragment>
        ))}
      </nav>
      {error && <p className="text-xs text-err-fg">{error}</p>}
      {loading ? (
        <p className="text-xs text-muted py-4">Loading folders…</p>
      ) : (
        <ul className="space-y-0.5 max-h-48 overflow-y-auto">
          {roots.map((folder) => (
            <TreeNode
              key={folder.folder_id}
              folder={folder}
              depth={0}
              byParent={byParent}
              selectedFolderId={selectedFolderId}
              documentCounts={documentCounts}
              onSelect={onSelect}
              onRenameClick={
                onRename
                  ? (f) => {
                      setRenameTarget(f);
                      setRenameValue(f.name);
                    }
                  : undefined
              }
              onDeleteClick={onDelete ? setDeleteTarget : undefined}
            />
          ))}
          {!roots.length && <li className="text-xs text-muted py-2">No folders yet.</li>}
        </ul>
      )}
      {onCreate && onNewFolderNameChange && (
        <div className="flex gap-1 pt-1 border-t border-line">
          <input
            value={newFolderName}
            onChange={(e) => onNewFolderNameChange(e.target.value)}
            placeholder="New folder"
            disabled={createBusy}
            className="flex-1 text-xs border border-line rounded px-2 py-1"
          />
          <button
            type="button"
            disabled={createBusy || !newFolderName.trim()}
            className="text-xs px-2 py-1 bg-soft rounded disabled:opacity-50"
            onClick={async () => {
              if (!newFolderName.trim()) return;
              setCreateBusy(true);
              try {
                await onCreate(newFolderName.trim(), parentFolderId || selectedFolderId || '');
                onNewFolderNameChange('');
              } finally {
                setCreateBusy(false);
              }
            }}
          >
            Add
          </button>
        </div>
      )}
      {renameTarget && (
        <div className="fixed inset-0 z-50 bg-scrim flex items-center justify-center p-4">
          <div className="bg-card rounded-xl p-4 w-full max-w-sm space-y-3">
            <p className="text-sm font-semibold">Rename folder</p>
            <input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} className="w-full text-sm border rounded px-2 py-1.5" autoFocus />
            <div className="flex justify-end gap-2">
              <button type="button" className="text-xs px-3 py-1.5" onClick={() => setRenameTarget(null)}>
                Cancel
              </button>
              <button type="button" disabled={renameBusy || !renameValue.trim()} className="text-xs px-3 py-1.5 bg-accent text-accentfg rounded-lg" onClick={submitRename}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 bg-scrim flex items-center justify-center p-4">
          <div className="bg-card rounded-xl p-4 w-full max-w-sm space-y-3">
            <p className="text-sm font-semibold">Delete “{deleteTarget.name}”?</p>
            <p className="text-xs text-muted">
              {childFolderCount(deleteTarget.folder_id, folders) > 0
                ? 'This folder has subfolders. Remove or move them first.'
                : (documentCounts?.[deleteTarget.folder_id] || 0) > 0
                  ? 'This folder has documents assigned. Move them before deleting.'
                  : 'This cannot be undone.'}
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" className="text-xs px-3 py-1.5" onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button
                type="button"
                disabled={
                  deleteBusy ||
                  childFolderCount(deleteTarget.folder_id, folders) > 0 ||
                  (documentCounts?.[deleteTarget.folder_id] || 0) > 0
                }
                className="text-xs px-3 py-1.5 bg-err-fg text-card rounded-lg disabled:opacity-40"
                onClick={submitDelete}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
