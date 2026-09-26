import { ALERT_TYPES, type SensorTemplate } from '../config/alertTypes';
import type { FactoryAlert, SensorReading, Severity, TriggerAlertInput } from '../types/alerts';
import type { FactoryZone } from '../types/zones';

/**
 * Turns a minimal TriggerAlertInput into a complete FactoryAlert, filling in
 * any fields the source did not provide with plausible fictional values.
 */

let sequence = 42;

function nextAlertId(date: Date): string {
  const id = `ALT-${date.getFullYear()}-${String(sequence).padStart(4, '0')}`;
  sequence += 1;
  return id;
}

function pick<T>(items: readonly T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

/** How far into the anomaly band the simulated value lands, per severity. */
const SEVERITY_INTENSITY: Record<Severity, number> = {
  low: 0.12,
  medium: 0.4,
  high: 0.7,
  critical: 0.95,
};

function formatNumber(value: number, decimals: number): string {
  return value.toFixed(decimals);
}

function generateReading(template: SensorTemplate, severity: Severity): SensorReading {
  if (template.kind === 'text') {
    return {
      label: template.label,
      value: template.value,
      expected: template.expected,
      outOfRange: template.outOfRange,
    };
  }
  const decimals = template.decimals ?? 0;
  const [a, b] = template.anomaly;
  const t = Math.min(1, Math.max(0, SEVERITY_INTENSITY[severity] + (Math.random() - 0.5) * 0.2));
  // Anomalies below the normal range get "worse" towards the lower bound.
  const belowRange = b <= template.min;
  const value = belowRange ? b - (b - a) * t : a + (b - a) * t;
  const unit = template.unit;
  return {
    label: template.label,
    value: `${formatNumber(value, decimals)} ${unit}`,
    expected: `${formatNumber(template.min, decimals)}–${formatNumber(template.max, decimals)} ${unit}`,
    outOfRange: true,
  };
}

function pickEquipment(zone: FactoryZone, hint: RegExp): string {
  const matching = zone.assets.filter((asset) => hint.test(asset));
  if (matching.length > 0) return pick(matching);
  if (zone.assets.length > 0) return pick(zone.assets);
  return `${zone.code}-EQ-01`;
}

export function createAlert(input: TriggerAlertInput, zone: FactoryZone): FactoryAlert {
  const definition = ALERT_TYPES[input.type];
  const timestamp = input.timestamp ?? new Date();
  const confidence = input.confidence ?? Math.round((0.82 + Math.random() * 0.15) * 100) / 100;

  return {
    id: nextAlertId(timestamp),
    zoneId: zone.id,
    type: input.type,
    severity: input.severity,
    timestamp,
    acknowledged: false,
    acknowledgedAt: null,
    equipmentId: input.equipmentId ?? pickEquipment(zone, definition.equipmentHint),
    description: input.description ?? pick(definition.descriptions),
    source: input.source ?? 'simulation',
    confidence: Math.min(1, Math.max(0, confidence)),
    sensorData: input.sensorData ?? definition.sensors.map((s) => generateReading(s, input.severity)),
    suggestedAction: input.suggestedAction ?? pick(definition.suggestedActions),
  };
}
