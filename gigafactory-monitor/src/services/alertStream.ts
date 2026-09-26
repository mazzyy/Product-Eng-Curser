import { isAlertType } from '../config/alertTypes';
import { isSeverity } from '../config/severity';
import type { AlertSource, SensorReading, TriggerAlertInput } from '../types/alerts';
import { triggerAlert } from './alertStore';

/**
 * ============================================================================
 *  FUTURE INTEGRATION POINT — ML / backend alert feed
 * ============================================================================
 *
 *   ML / anomaly detection ─► backend ─► WebSocket ─► parseAlertEvent ─► triggerAlert(...) ─► UI
 *
 * Enabled when VITE_ALERT_WS_URL is set (e.g. in `.env.local`):
 *     VITE_ALERT_WS_URL=ws://localhost:8080/alerts
 *
 * Expected message (JSON, one alert per message, or an array of them):
 *   {
 *     "zoneId": "a103-body-in-white",      // must match an id in factoryZones.ts
 *     "type": "equipment_failure",          // an AlertType
 *     "severity": "high",                   // low | medium | high | critical
 *     "equipmentId": "Robot Cell BIW-R17",  // optional
 *     "description": "Torque threshold exceeded", // optional
 *     "confidence": 0.93,                   // optional, 0–1
 *     "timestamp": "2026-09-25T14:32:06Z",  // optional, ISO 8601
 *     "sensorData": [{ "label": "Motor current", "value": "42 A", "expected": "18–30 A", "outOfRange": true }],
 *     "suggestedAction": "Inspect motor drive" // optional
 *   }
 * Messages wrapped as { "event": "alert", "data": { ... } } are accepted too.
 */

export type StreamStatus = 'disabled' | 'connecting' | 'connected' | 'reconnecting';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseSensorData(value: unknown): SensorReading[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const readings: SensorReading[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.label !== 'string') continue;
    readings.push({
      label: item.label,
      value: String(item.value ?? '—'),
      expected: typeof item.expected === 'string' ? item.expected : undefined,
      outOfRange: item.outOfRange !== false,
    });
  }
  return readings.length > 0 ? readings : undefined;
}

/** Validates one raw event. Returns null when it cannot be turned into an alert. */
export function parseAlertEvent(raw: unknown, source: AlertSource = 'ml'): TriggerAlertInput | null {
  const data = isRecord(raw) && raw.event === 'alert' && isRecord(raw.data) ? raw.data : raw;
  if (!isRecord(data)) return null;
  const { zoneId, type, severity } = data;
  if (typeof zoneId !== 'string' || !isAlertType(type) || !isSeverity(severity)) return null;

  const timestamp = typeof data.timestamp === 'string' ? new Date(data.timestamp) : undefined;
  return {
    zoneId,
    type,
    severity,
    source,
    equipmentId: typeof data.equipmentId === 'string' ? data.equipmentId : undefined,
    description: typeof data.description === 'string' ? data.description : undefined,
    confidence: typeof data.confidence === 'number' ? data.confidence : undefined,
    suggestedAction: typeof data.suggestedAction === 'string' ? data.suggestedAction : undefined,
    sensorData: parseSensorData(data.sensorData),
    timestamp: timestamp && !Number.isNaN(timestamp.getTime()) ? timestamp : undefined,
  };
}

/**
 * Connects to the alert WebSocket and forwards valid events to triggerAlert.
 * Reconnects with exponential backoff. Returns a disconnect function.
 */
export function connectAlertStream(url: string, onStatus?: (status: StreamStatus) => void): () => void {
  let socket: WebSocket | null = null;
  let retryTimer: number | undefined;
  let attempt = 0;
  let disposed = false;

  const connect = () => {
    if (disposed) return;
    onStatus?.(attempt === 0 ? 'connecting' : 'reconnecting');
    try {
      socket = new WebSocket(url);
    } catch (error) {
      console.warn('[alert-stream] Could not open WebSocket', error);
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      attempt = 0;
      onStatus?.('connected');
    };

    socket.onmessage = (message: MessageEvent) => {
      let payload: unknown;
      try {
        payload = JSON.parse(String(message.data));
      } catch {
        console.warn('[alert-stream] Ignoring non-JSON message');
        return;
      }
      const events = Array.isArray(payload) ? payload : [payload];
      for (const event of events) {
        const input = parseAlertEvent(event, 'ml');
        if (input) triggerAlert(input);
        else console.warn('[alert-stream] Ignoring invalid alert event', event);
      }
    };

    socket.onclose = () => {
      socket = null;
      scheduleReconnect();
    };

    socket.onerror = () => {
      socket?.close();
    };
  };

  const scheduleReconnect = () => {
    if (disposed) return;
    attempt += 1;
    onStatus?.('reconnecting');
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt - 1, 5));
    retryTimer = window.setTimeout(connect, delay);
  };

  connect();

  return () => {
    disposed = true;
    window.clearTimeout(retryTimer);
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
    onStatus?.('disabled');
  };
}
