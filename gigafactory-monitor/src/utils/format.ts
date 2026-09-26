const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

/** 14:32:06 */
export function formatTime(date: Date): string {
  return timeFormatter.format(date);
}

/** Fri, 25 Sept 2026 → normalised to "Fri 25 Sep 2026" */
export function formatDate(date: Date): string {
  return dateFormatter.format(date).replace(',', '').replace('Sept', 'Sep');
}

/** "just now", "45 s ago", "3 min ago", "2 h ago" */
export function formatAge(date: Date, now: Date): string {
  const seconds = Math.max(0, Math.round((now.getTime() - date.getTime()) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}

/** 0.94 → "94%" */
export function formatConfidence(value: number): string {
  return `${Math.round(value * 100)}%`;
}
