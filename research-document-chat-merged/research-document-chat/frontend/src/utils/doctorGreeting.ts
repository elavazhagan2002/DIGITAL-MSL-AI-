/** Personalized welcome copy for doctor chat (empty conversation only). */

export function timeOfDayGreeting(date: Date = new Date()): string {
  const hour = date.getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

/** Returns salutation name; avoids doubling "Dr." prefix. */
export function formatDoctorSalutation(displayName: string): string | null {
  const trimmed = displayName.trim();
  if (!trimmed) return null;
  const base = trimmed.split(',')[0].trim();
  if (!base) return null;
  if (/^dr\.?\s/i.test(base)) return base;
  const withoutTitle = base.replace(/^Dr\.?\s+/i, '').trim();
  if (!withoutTitle) return null;
  return `Dr. ${withoutTitle}`;
}

export function buildDoctorWelcomeGreeting(displayName?: string, now: Date = new Date()): string[] {
  const salutation = displayName ? formatDoctorSalutation(displayName) : null;
  if (!salutation) {
    return [
      'Welcome to Digital MSL!',
      'I am your scientific information assistant. How can I help you today?',
    ];
  }
  return [
    `${timeOfDayGreeting(now)}, ${salutation}!`,
    'Welcome to Digital MSL, your scientific information assistant.',
    'I can help you explore your authorized medical and clinical document repository, find supporting scientific evidence, and answer questions based on available documents.',
    'How can I help you today?',
  ];
}
