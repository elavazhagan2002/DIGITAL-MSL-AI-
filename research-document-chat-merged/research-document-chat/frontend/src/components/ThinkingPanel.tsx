import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, Loader2, XCircle, Ban } from 'lucide-react';
import type { ThinkingStep, DraftingStats } from '../types/api';

interface ThinkingPanelProps {
  steps: ThinkingStep[];
  isStreaming: boolean;
  defaultCollapsed?: boolean;
  draftingStats?: DraftingStats;
}

function formatDuration(seconds?: number): string {
  if (seconds == null || Number.isNaN(seconds)) return '';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

export default function ThinkingPanel({
  steps,
  isStreaming,
  defaultCollapsed = false,
  draftingStats,
}: ThinkingPanelProps) {
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);
  const contentRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const contentFingerprint = steps.map((s) => `${s.tool}:${s.status}:${s.label}`).join('|');

  // Always follow the latest step while streaming; user can scroll up to pause follow.
  useEffect(() => {
    if (!isCollapsed && isStreaming && bottomRef.current) {
      bottomRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [contentFingerprint, isCollapsed, isStreaming]);

  useEffect(() => {
    if (!isCollapsed && !isStreaming && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [contentFingerprint, isCollapsed, isStreaming, draftingStats]);

  useEffect(() => {
    if (steps.length > 0 && !defaultCollapsed) {
      setIsCollapsed(false);
    }
  }, [steps.length, defaultCollapsed]);

  if (steps.length === 0) return null;

  const allDone = !isStreaming && steps.every((s) => s.status === 'done');
  const hasFailure = steps.some((s) => s.status === 'failed' || s.status === 'cancelled');
  const doneCount = steps.filter((s) => s.status === 'done').length;
  const showStats =
    !isStreaming &&
    (draftingStats?.duration_sec != null || draftingStats?.llm_usage?.total_tokens != null);

  return (
    <div className="w-full mb-3 rounded-xl border border-line bg-gradient-to-b from-brandcyan to-brandcyan overflow-hidden transition-all duration-300">
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-warn-bg transition-colors duration-200"
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {allDone ? (
            <div className="w-5 h-5 rounded-full bg-selbg flex items-center justify-center flex-shrink-0">
              <Check className="w-3 h-3 text-link" />
            </div>
          ) : hasFailure && !isStreaming ? (
            <div className="w-5 h-5 rounded-full bg-err-bg flex items-center justify-center flex-shrink-0">
              <XCircle className="w-3 h-3 text-err-fg" />
            </div>
          ) : (
            <div className="w-5 h-5 flex items-center justify-center flex-shrink-0">
              <span className="relative flex h-5 w-5 items-center justify-center">
                <span className="absolute inset-0 rounded-full bg-warn-fg animate-ping" />
                <span className="relative flex items-center gap-0.5">
                  <span className="h-1 w-1 rounded-full bg-warn-fg animate-bounce [animation-delay:0ms]" />
                  <span className="h-1 w-1 rounded-full bg-warn-fg animate-bounce [animation-delay:120ms]" />
                  <span className="h-1 w-1 rounded-full bg-warn-fg animate-bounce [animation-delay:240ms]" />
                </span>
              </span>
            </div>
          )}
          <span
            className={`text-sm font-medium ${
              allDone
                ? 'text-link'
                : hasFailure && !isStreaming
                  ? 'text-err-fg'
                  : 'text-warn-fg'
            }`}
          >
            {allDone
              ? 'Thought process complete'
              : hasFailure && !isStreaming
                ? 'Drafting stopped with errors'
                : 'Thinking...'}
          </span>
          <span className="text-xs text-muted">
            {doneCount}/{steps.length} steps
          </span>
        </div>
        <ChevronDown
          className={`w-4 h-4 text-muted transition-transform duration-200 flex-shrink-0 ${
            isCollapsed ? '-rotate-90' : ''
          }`}
        />
      </button>

      {!isCollapsed && (
        <div
          ref={contentRef}
          className="max-h-[240px] overflow-y-auto px-4 pb-3 space-y-1 scrollbar-thin"
          style={{ scrollbarWidth: 'thin' }}
        >
          {steps.map((step, index) => (
            <div key={`${step.tool}-${index}`} className="flex items-start gap-2 py-0.5">
              <div className="flex-shrink-0 mt-0.5">
                {step.status === 'done' ? (
                  <Check className="w-3.5 h-3.5 text-brandcyan" />
                ) : step.status === 'failed' ? (
                  <XCircle className="w-3.5 h-3.5 text-err-fg" />
                ) : step.status === 'cancelled' ? (
                  <Ban className="w-3.5 h-3.5 text-muted" />
                ) : (
                  <Loader2 className="w-3.5 h-3.5 text-warn-fg animate-spin" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p
                  className={`text-xs leading-snug break-words ${
                    step.status === 'done'
                      ? 'text-muted'
                      : step.status === 'failed'
                        ? 'text-err-fg'
                        : step.status === 'cancelled'
                          ? 'text-muted line-through'
                          : 'text-fg font-medium'
                  }`}
                >
                  {step.label}
                  {step.status === 'in-progress' && (
                    <span className="inline-block w-1 h-3 ml-0.5 bg-warn-fg animate-pulse align-middle" />
                  )}
                </p>
              </div>
            </div>
          ))}

          {showStats && (
            <div className="mt-2 pt-2 border-t border-warn-line text-[11px] text-muted">
              Completed in {formatDuration(draftingStats?.duration_sec)}
              {draftingStats?.llm_usage?.total_tokens != null && (
                <span>
                  {' '}
                  · {draftingStats.llm_usage.total_tokens.toLocaleString()} tokens
                  {draftingStats.llm_usage.llm_call_count
                    ? ` (${draftingStats.llm_usage.llm_call_count} LLM calls)`
                    : ''}
                </span>
              )}
            </div>
          )}

          <div ref={bottomRef} aria-hidden="true" className="h-px" />
        </div>
      )}
    </div>
  );
}
