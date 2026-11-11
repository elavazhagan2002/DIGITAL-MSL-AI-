import { useEffect, useRef, useState } from 'react';
import { getDocument, GlobalWorkerOptions, Util } from 'pdfjs-dist';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { API_BASE_URL, API_ENDPOINTS } from '../config/api';
import type { ClaimEvidence } from '../services/digitalMslService';

GlobalWorkerOptions.workerSrc = workerSrc;

const pdfCache = new Map<string, Promise<Uint8Array>>();

function fetchPdfBytes(documentId: string): Promise<Uint8Array> {
  const existing = pdfCache.get(documentId);
  if (existing) return existing;
  const pending = fetch(`${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_RENDER(documentId)}`).then(async (res) => {
    if (!res.ok) throw new Error('Could not open the source document.');
    return new Uint8Array(await res.arrayBuffer());
  });
  pdfCache.set(documentId, pending);
  pending.catch(() => pdfCache.delete(documentId));
  return pending;
}

function normalizeText(value: string): string {
  return (value || '')
    .toLowerCase()
    .replace(/(\w)-\s+(\w)/g, '$1$2')
    .replace(/[^a-z0-9.%]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function contentTerms(value: string): string[] {
  const stop = new Set('a an and are as at be by for from in into is it its of on or the to was were with'.split(' '));
  return normalizeText(value)
    .split(' ')
    .filter((token) => token.length > 2 && !stop.has(token) && !/^\d+$/.test(token));
}

function isCitationLike(value: string): boolean {
  const text = (value || '').trim();
  if (!text) return true;
  return /\bet al\.?/i.test(text) && /\b(19|20)\d{2}\b/.test(text) && (/\d+:\d+/.test(text) || /n engl j med|ophthalmology|diabetes care/i.test(text));
}

function isBibliographyPage(text: string): boolean {
  const etAl = (text.match(/\bet al\b/gi) || []).length;
  const years = (text.match(/\b(19|20)\d{2}\b/g) || []).length;
  return /\breferences\b/i.test(text) || (etAl >= 4 && years >= 4);
}

function overlapScore(haystack: string, needle: string): number {
  const hay = normalizeText(haystack);
  const terms = contentTerms(needle);
  if (!hay || !terms.length) return 0;
  const present = terms.filter((term) => hay.includes(term)).length;
  const snippet = normalizeText(needle).slice(0, 80);
  const exact = snippet.length >= 18 && hay.includes(snippet) ? 0.35 : 0;
  return present / terms.length + exact;
}

function matchingItemIndexes(strings: string[], quote: string): Set<number> {
  const highlighted = new Set<number>();
  const needle = normalizeText(quote);
  if (!needle) return highlighted;
  const tokens = strings.map((str, index) => ({ index, text: normalizeText(str) })).filter((t) => t.text);
  const full = tokens.map((t) => t.text).join(' ');
  const snippets = [needle, needle.slice(0, 120), needle.slice(0, 72), needle.slice(0, 40)].filter((s) => s.length >= 16);
  let start = -1;
  let length = 0;
  for (const snippet of snippets) {
    start = full.indexOf(snippet);
    if (start >= 0) {
      length = snippet.length;
      break;
    }
  }
  if (start < 0) {
    const terms = contentTerms(quote);
    if (terms.length < 4) return highlighted;
    let window: number[] = [];
    let hits = 0;
    tokens.forEach((token) => {
      const matched = terms.some((term) => token.text.includes(term));
      if (matched) {
        window.push(token.index);
        hits += 1;
      } else if (window.length && hits / terms.length < 0.35) {
        window = [];
        hits = 0;
      }
    });
    if (hits / terms.length >= 0.45) window.forEach((index) => highlighted.add(index));
    return highlighted;
  }
  const end = start + length;
  let cursor = 0;
  for (const token of tokens) {
    const from = cursor;
    const to = cursor + token.text.length;
    if (to > start && from < end) highlighted.add(token.index);
    cursor = to + 1;
  }
  return highlighted;
}

function itemBox(item: { transform: number[]; width?: number; height?: number; str?: string }, viewport: { transform: number[]; scale: number }) {
  const tx = Util.transform(viewport.transform, item.transform);
  const height = Math.hypot(tx[2], tx[3]) || (item.height || 8) * viewport.scale;
  const width = (item.width || 0) * Math.hypot(tx[0], tx[1]) || Math.max((item.str || '').length * height * 0.45, 4);
  return {
    left: tx[4],
    top: tx[5] - height,
    width: Math.max(width, 4),
    height: Math.max(height * 1.15, 8),
  };
}

function mergeBoxes(boxes: { left: number; top: number; width: number; height: number }[]) {
  if (!boxes.length) return boxes;
  const sorted = [...boxes].sort((a, b) => a.top - b.top || a.left - b.left);
  const merged: typeof boxes = [];
  for (const box of sorted) {
    const prev = merged[merged.length - 1];
    const sameLine = prev && Math.abs(prev.top - box.top) < Math.max(prev.height, box.height) * 0.7;
    const nextLine = prev && box.top - (prev.top + prev.height) < Math.max(prev.height, box.height) * 0.85;
    const adjacent = prev && box.left <= prev.left + prev.width + 12;
    const overlappingX = prev && box.left < prev.left + prev.width + 40;
    if ((sameLine && adjacent) || (nextLine && overlappingX)) {
      const right = Math.max(prev.left + prev.width, box.left + box.width);
      const bottom = Math.max(prev.top + prev.height, box.top + box.height);
      prev.left = Math.min(prev.left, box.left);
      prev.top = Math.min(prev.top, box.top);
      prev.width = right - prev.left;
      prev.height = bottom - prev.top;
    } else {
      merged.push({ ...box });
    }
  }
  return merged;
}

interface OverlayBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function SourcePdfViewer({ evidence }: { evidence: ClaimEvidence }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [boxes, setBoxes] = useState<OverlayBox[]>([]);
  const [pageLabel, setPageLabel] = useState('');
  const [hostWidth, setHostWidth] = useState(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const update = () => {
      const next = Math.max(Math.floor(host.clientWidth / 16) * 16, 0);
      setHostWidth((prev) => (prev === next ? prev : next));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!evidence.document_id) {
      setError('This evidence is not linked to a source document.');
      setLoading(false);
      return undefined;
    }
    if (!hostWidth) return undefined;
    let cancelled = false;
    setLoading(true);
    setError('');
    setBoxes([]);

    const render = async () => {
      const host = hostRef.current;
      const canvas = canvasRef.current;
      if (!host || !canvas) return;
      const stored = await fetchPdfBytes(evidence.document_id);
      if (cancelled) return;
      const data = stored.slice();
      const pdf = await getDocument({ data, disableAutoFetch: true, disableStream: true }).promise;
      if (cancelled) return;

      const quote = evidence.original_text || '';
      const claim = evidence.claim_text || '';
      const body = evidence.document_content || '';
      const needles = [quote, claim, body.slice(0, 280)].filter((value) => value && !isCitationLike(value));
      if (!needles.length) needles.push(claim || quote);

      type PageHit = { pageNumber: number; text: string; score: number };
      const hits: PageHit[] = [];
      const maxPages = Math.min(pdf.numPages, 40);
      for (let pageNumber = 1; pageNumber <= maxPages; pageNumber += 1) {
        const page = await pdf.getPage(pageNumber);
        const textContent = await page.getTextContent({ includeMarkedContent: false });
        const text = textContent.items.map((item) => ('str' in item ? item.str : '')).join(' ');
        let score = Math.max(...needles.map((needle) => overlapScore(text, needle)), 0);
        if (isBibliographyPage(text)) score -= 0.45;
        if (pageNumber === (evidence.page_no || 0) && !isBibliographyPage(text)) score += 0.08;
        hits.push({ pageNumber, text, score });
      }
      hits.sort((a, b) => b.score - a.score);
      const chosen = hits[0]?.score > 0.2 ? hits[0] : hits.find((hit) => hit.pageNumber === (evidence.page_no || 1)) || hits[0];
      const pageNumber = chosen?.pageNumber || Math.min(Math.max(evidence.page_no || 1, 1), pdf.numPages);
      const page = await pdf.getPage(pageNumber);
      const width = Math.max(hostWidth - 16, 320);
      const unscaled = page.getViewport({ scale: 1 });
      const scale = width / unscaled.width;
      const viewport = page.getViewport({ scale });
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) return;
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.style.width = `${canvas.width}px`;
      canvas.style.height = `${canvas.height}px`;
      await page.render({ canvas, viewport, canvasContext: context, background: 'white' }).promise;
      if (cancelled) return;
      const textContent = await page.getTextContent({ includeMarkedContent: false });
      const strings = textContent.items.map((item) => ('str' in item ? item.str : ''));
      let matched = new Set<number>();
      for (const needle of needles) {
        matched = matchingItemIndexes(strings, needle);
        if (matched.size) break;
      }
      const nextBoxes: OverlayBox[] = [];
      textContent.items.forEach((item, index) => {
        if (!matched.has(index) || !('transform' in item)) return;
        nextBoxes.push(itemBox(item as { transform: number[]; width?: number; height?: number; str?: string }, viewport));
      });
      setBoxes(mergeBoxes(nextBoxes));
      setPageLabel(`Original page ${pageNumber} of ${pdf.numPages}`);
      setLoading(false);
      requestAnimationFrame(() => {
        const first = host.querySelector('[data-cite-highlight]') as HTMLElement | null;
        first?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    };

    render().catch((err) => {
      if (!cancelled) {
        setError(err instanceof Error ? err.message : 'Could not open the source document.');
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [evidence.document_id, evidence.page_no, evidence.original_text, evidence.document_content, evidence.claim_text, hostWidth]);

  return (
    <div ref={hostRef} className="flex-1 min-h-0 overflow-auto bg-line">
      {loading && <p className="p-4 text-xs text-muted">Opening original document…</p>}
      {error && <p className="p-4 text-xs text-err-fg">{error}</p>}
      {!loading && !error && pageLabel && <p className="px-4 pt-3 text-[11px] uppercase tracking-wide text-muted">{pageLabel}</p>}
      <div className="relative mx-auto my-3 w-fit">
        <canvas ref={canvasRef} className="block bg-card shadow" />
        {boxes.map((box, i) => (
          <div
            key={`${box.left}-${box.top}-${i}`}
            data-cite-highlight
            className="absolute bg-warn-fg pointer-events-none mix-blend-multiply"
            style={{
              left: box.left,
              top: box.top,
              width: box.width,
              height: box.height,
            }}
          />
        ))}
      </div>
    </div>
  );
}
