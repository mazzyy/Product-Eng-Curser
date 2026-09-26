import { severityRank } from '../config/severity';
import type { FactoryAlert, Severity } from '../types/alerts';

/** CRITICAL → HIGH → MEDIUM → LOW, unacknowledged first, then newest first. */
export function compareAlerts(a: FactoryAlert, b: FactoryAlert): number {
  const bySeverity = severityRank(b.severity) - severityRank(a.severity);
  if (bySeverity !== 0) return bySeverity;
  if (a.acknowledged !== b.acknowledged) return a.acknowledged ? 1 : -1;
  return b.timestamp.getTime() - a.timestamp.getTime();
}

export function sortAlerts(alerts: readonly FactoryAlert[]): FactoryAlert[] {
  return [...alerts].sort(compareAlerts);
}

export function alertsForZone(alerts: readonly FactoryAlert[], zoneId: string): FactoryAlert[] {
  return sortAlerts(alerts.filter((a) => a.zoneId === zoneId));
}

export interface ZoneAlertSummary {
  zoneId: string;
  count: number;
  unacknowledged: number;
  /** Highest unacknowledged severity, or highest overall if all are acknowledged. */
  severity: Severity;
  /** True when every alert in the zone has been acknowledged. */
  allAcknowledged: boolean;
}

export function summarizeByZone(alerts: readonly FactoryAlert[]): Record<string, ZoneAlertSummary> {
  const result: Record<string, ZoneAlertSummary> = {};
  for (const zoneAlerts of groupByZone(alerts).values()) {
    const open = zoneAlerts.filter((a) => !a.acknowledged);
    const pool = open.length > 0 ? open : zoneAlerts;
    const worst = pool.reduce((best, a) => (severityRank(a.severity) > severityRank(best.severity) ? a : best));
    const zoneId = zoneAlerts[0].zoneId;
    result[zoneId] = {
      zoneId,
      count: zoneAlerts.length,
      unacknowledged: open.length,
      severity: worst.severity,
      allAcknowledged: open.length === 0,
    };
  }
  return result;
}

function groupByZone(alerts: readonly FactoryAlert[]): Map<string, FactoryAlert[]> {
  const map = new Map<string, FactoryAlert[]>();
  for (const alert of alerts) {
    const list = map.get(alert.zoneId);
    if (list) list.push(alert);
    else map.set(alert.zoneId, [alert]);
  }
  return map;
}

export function highestSeverity(alerts: readonly FactoryAlert[]): Severity | null {
  let worst: Severity | null = null;
  for (const alert of alerts) {
    if (!worst || severityRank(alert.severity) > severityRank(worst)) worst = alert.severity;
  }
  return worst;
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
