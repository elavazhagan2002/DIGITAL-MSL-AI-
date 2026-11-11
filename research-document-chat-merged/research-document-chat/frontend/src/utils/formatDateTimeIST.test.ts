import { describe, expect, it } from 'vitest';
import { formatDateTimeIST, parseApiDate } from '../utils/formatDateTimeIST';

describe('formatDateTimeIST', () => {
  it('treats naive ISO as UTC and formats in IST', () => {
    const d = parseApiDate('2026-03-27T04:04:00');
    expect(d).not.toBeNull();
    const label = formatDateTimeIST('2026-03-27T04:04:00');
    expect(label).toMatch(/27/);
    expect(label).toMatch(/2026/);
    // 04:04 UTC = 09:34 IST
    expect(label.toLowerCase()).toMatch(/9:34|09:34/);
  });
});
