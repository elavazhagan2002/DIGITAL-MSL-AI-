interface TypingIndicatorProps {
  label?: string;
  className?: string;
}

export default function TypingIndicator({
  label = 'Thinking',
  className = '',
}: TypingIndicatorProps) {
  return (
    <div
      className={`flex items-center gap-3 ${className}`}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="relative flex h-8 w-8 items-center justify-center">
        <span className="absolute inset-0 rounded-full bg-navyactive animate-ping" />
        <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brandcyan to-brandnavy shadow-sm">
          <span className="flex items-center gap-0.5">
            <span className="h-1.5 w-1.5 rounded-full bg-card animate-bounce [animation-delay:0ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-card animate-bounce [animation-delay:150ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-card animate-bounce [animation-delay:300ms]" />
          </span>
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-link">{label}</span>
        <span className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-accent animate-bounce [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent animate-bounce [animation-delay:120ms]" />
          <span className="h-1.5 w-1.5 rounded-full bg-accent animate-bounce [animation-delay:240ms]" />
        </span>
      </div>
    </div>
  );
}
