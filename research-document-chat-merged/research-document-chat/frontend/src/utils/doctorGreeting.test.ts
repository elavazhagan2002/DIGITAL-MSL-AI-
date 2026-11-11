import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BrandMark } from '../portal/BrandMark';
import { buildDoctorWelcomeGreeting, formatDoctorSalutation, timeOfDayGreeting } from './doctorGreeting';

describe('doctorGreeting', () => {
  it('uses time-of-day salutation', () => {
    expect(timeOfDayGreeting(new Date('2026-03-10T09:00:00'))).toBe('Good morning');
    expect(timeOfDayGreeting(new Date('2026-03-10T14:00:00'))).toBe('Good afternoon');
    expect(timeOfDayGreeting(new Date('2026-03-10T19:00:00'))).toBe('Good evening');
  });

  it('personalizes with doctor name', () => {
    const lines = buildDoctorWelcomeGreeting('Priya Raman', new Date('2026-03-10T09:00:00'));
    expect(lines[0]).toBe('Good morning, Dr. Priya Raman!');
    expect(lines).toHaveLength(4);
  });

  it('does not double Dr prefix', () => {
    expect(formatDoctorSalutation('Dr. Arjun Mehta')).toBe('Dr. Arjun Mehta');
  });

  it('falls back without a name', () => {
    const lines = buildDoctorWelcomeGreeting('', new Date('2026-03-10T12:00:00'));
    expect(lines[0]).toBe('Welcome to Digital MSL!');
  });
});

describe('brand mark', () => {
  it('shows only the sciagen wordmark when the product label is hidden', () => {
    const html = renderToStaticMarkup(createElement(BrandMark, { showSubtitle: false }));
    expect(html).not.toContain('Digital MSL');
  });

  it('shows the Digital MSL product label by default', () => {
    const html = renderToStaticMarkup(createElement(BrandMark, { showSubtitle: true }));
    expect(html).toContain('Digital MSL');
  });
});
