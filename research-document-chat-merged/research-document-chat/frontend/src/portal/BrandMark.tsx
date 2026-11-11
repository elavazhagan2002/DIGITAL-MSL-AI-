/**
 * Brand mark components for Digital MSL.
 */
export function SciagenLogo({ light = false, className = 'h-8' }: { light?: boolean; className?: string }) {
  return null;
}

export function SciagenMark({ light = false, className = 'h-9' }: { light?: boolean; className?: string }) {
  return null;
}

export function BrandMark({
  size = 'md',
  light = false,
  showSubtitle = true,
}: {
  size?: 'sm' | 'md' | 'lg';
  light?: boolean;
  showSubtitle?: boolean;
}) {
  if (!showSubtitle) return null;
  return (
    <div className="flex flex-col items-start gap-1.5">
      <p className={`text-[12px] font-bold uppercase tracking-[0.2em] ${light ? 'text-brandcyan' : 'text-link'}`}>
        Digital MSL
      </p>
    </div>
  );
}
