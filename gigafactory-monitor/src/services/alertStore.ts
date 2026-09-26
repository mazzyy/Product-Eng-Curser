import { isAlertType } from '../config/alertTypes';
import { getZone } from '../config/factoryZones';
import { isSeverity } from '../config/severity';
import type { FactoryAlert, TriggerAlertInput } from '../types/alerts';
import { createAlert } from './alertFactory';

/**
 * ============================================================================
 *  ALERT STORE — the single entry point for alerts
 * ============================================================================
 *
 *   Manual Simulator ─┐
 *   Demo scenarios ───┼──►  triggerAlert({ zoneId, type, severity })  ──►  UI
 *   ML / WebSocket ───┘         (this file)                   (map, list, sound)
 *
 * Nothing in the UI creates alerts directly. Anything that wants to raise an
 * alert calls `triggerAlert(...)`; React components read the state through
 * `useAlerts()` and react to new alerts through `onAlertTriggered(...)`.
 *
 * The store lives outside React on purpose, so non-React code (WebSocket
 * clients, timers, tests, the browser console) can use it too.
 */

type Listener = () => void;
type TriggerListener = (alert: FactoryAlert) => void;

let alerts: readonly FactoryAlert[] = [];
const listeners = new Set<Listener>();
const triggerListeners = new Set<TriggerListener>();

function commit(next: readonly FactoryAlert[]): void {
  alerts = next;
  listeners.forEach((listener) => listener());
}

/**
 * Raise a new alert. Returns the created alert, or null if the input is
 * invalid (unknown zone / type / severity), in which case nothing changes.
 */
export function triggerAlert(input: TriggerAlertInput): FactoryAlert | null {
  const zone = getZone(input.zoneId);
  if (!zone) {
    console.warn(`[alerts] Ignoring alert for unknown zone "${input.zoneId}"`);
    return null;
  }
  if (!isAlertType(input.type)) {
    console.warn(`[alerts] Ignoring alert with unknown type "${String(input.type)}"`);
    return null;
  }
  if (!isSeverity(input.severity)) {
    console.warn(`[alerts] Ignoring alert with unknown severity "${String(input.severity)}"`);
    return null;
  }

  const alert = createAlert(input, zone);
  commit([...alerts, alert]);
  triggerListeners.forEach((listener) => listener(alert));
  return alert;
}

/** Marks an alert as acknowledged. It stays in the list and on the map. */
export function acknowledgeAlert(alertId: string): void {
  if (!alerts.some((a) => a.id === alertId && !a.acknowledged)) return;
  const now = new Date();
  commit(alerts.map((a) => (a.id === alertId ? { ...a, acknowledged: true, acknowledgedAt: now } : a)));
}

/** Removes a single alert (e.g. resolved by the backend). */
export function resolveAlert(alertId: string): void {
  if (!alerts.some((a) => a.id === alertId)) return;
  commit(alerts.filter((a) => a.id !== alertId));
}

/** Removes every alert. */
export function clearAlerts(): void {
  if (alerts.length === 0) return;
  commit([]);
}

/** Current snapshot. The array reference only changes when the data changes. */
export function getAlerts(): readonly FactoryAlert[] {
  return alerts;
}

/** Subscribe to any change of the alert list. Returns an unsubscribe function. */
export function subscribeToAlerts(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Subscribe to newly triggered alerts (zoom, sound, focus). */
export function onAlertTriggered(listener: TriggerListener): () => void {
  triggerListeners.add(listener);
  return () => {
    triggerListeners.delete(listener);
  };
}

// Handy for demos: `window.factoryAlerts.triggerAlert({ zoneId: 'a102-casting', type: 'temperature_anomaly', severity: 'medium' })`
declare global {
  interface Window {
    factoryAlerts?: {
      triggerAlert: typeof triggerAlert;
      acknowledgeAlert: typeof acknowledgeAlert;
      resolveAlert: typeof resolveAlert;
      clearAlerts: typeof clearAlerts;
      getAlerts: typeof getAlerts;
    };
  }
}

if (typeof window !== 'undefined') {
  window.factoryAlerts = { triggerAlert, acknowledgeAlert, resolveAlert, clearAlerts, getAlerts };
}
