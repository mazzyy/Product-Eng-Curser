import type { Severity } from '../types/alerts';

export interface SeverityMeta {
  id: Severity;
  /** Upper-case label shown next to the icon, e.g. "CRITICAL". */
  label: string;
  /** Title-case label for prose, e.g. "Critical". */
  title: string;
  /** Higher = more severe. Used for sorting and sound priority. */
  rank: number;
  /** CSS custom property holding the accent colour. */
  colorVar: string;
  /** Human description of the audible pattern (legend / tooltips). */
  soundLabel: string;
}

/** Ordered from least to most severe (UI order for the severity buttons). */
export const SEVERITIES: Severity[] = ['low', 'medium', 'high', 'critical'];

export const SEVERITY_META: Record<Severity, SeverityMeta> = {
  low: {
    id: 'low',
    label: 'LOW',
    title: 'Low',
    rank: 1,
    colorVar: 'var(--sev-low)',
    soundLabel: 'Single quiet tone',
  },
  medium: {
    id: 'medium',
    label: 'MEDIUM',
    title: 'Medium',
    rank: 2,
    colorVar: 'var(--sev-medium)',
    soundLabel: 'Double beep',
  },
  high: {
    id: 'high',
    label: 'HIGH',
    title: 'High',
    rank: 3,
    colorVar: 'var(--sev-high)',
    soundLabel: 'Triple beep',
  },
  critical: {
    id: 'critical',
    label: 'CRITICAL',
    title: 'Critical',
    rank: 4,
    colorVar: 'var(--sev-critical)',
    soundLabel: 'Repeating alarm (~3.5 s)',
  },
};

export function severityRank(severity: Severity): number {
  return SEVERITY_META[severity].rank;
}

export function isSeverity(value: unknown): value is Severity {
  return typeof value === 'string' && value in SEVERITY_META;
}
