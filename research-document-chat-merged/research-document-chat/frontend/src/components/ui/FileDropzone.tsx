import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { UploadCloud } from 'lucide-react';

/**
 * Drag-and-drop area that is also a real button (keyboard and screen-reader friendly).
 * Shows the accepted types and the size limit so nobody discovers them by failing.
 */
export function FileDropzone({
  onFiles,
  accept,
  hint,
  disabled = false,
  disabledReason,
}: {
  onFiles: (files: File[]) => void;
  accept: string;
  hint: string;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const open = () => {
    if (!disabled) input.current?.click();
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) onFiles(files);
  };

  return (
    <div>
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-label="Choose files to load, or drop them here"
        data-testid="dropzone"
        onClick={open}
        onKeyDown={onKey}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex min-h-[132px] flex-col items-center justify-center gap-2 rounded-[14px] border-2 border-dashed px-6 py-6 text-center transition-colors ${
          disabled
            ? 'cursor-not-allowed border-line bg-soft opacity-70'
            : over
              ? 'cursor-pointer border-selb bg-selbg'
              : 'cursor-pointer border-fieldb bg-card hover:bg-selbg'
        }`}
      >
        <UploadCloud className="h-7 w-7 text-link" aria-hidden="true" />
        <p className="text-sm font-semibold text-fg">{disabled ? 'Loading is not available right now' : 'Drop files here, or choose files'}</p>
        <p className="text-xs text-muted">{disabled && disabledReason ? disabledReason : hint}</p>
      </div>
      <input
        ref={input}
        type="file"
        multiple
        accept={accept}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        data-testid="file-input"
        disabled={disabled}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = '';
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}
