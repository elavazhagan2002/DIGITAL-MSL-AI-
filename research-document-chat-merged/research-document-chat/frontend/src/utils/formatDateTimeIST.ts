/** Parse API timestamps (UTC, often without offset) and format in India Standard Time. */
export function parseApiDate(iso: string): Date | null {
  const raw = (iso || '').trim();
  if (!raw) return null;
  const hasOffset = raw.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(raw);
  const normalized = hasOffset ? raw : `${raw}Z`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTimeIST(iso: string | null | undefined): string {
  const d = iso ? parseApiDate(iso) : null;
  if (!d) return '';
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}
