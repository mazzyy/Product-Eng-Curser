/**
 * Core alert data model.
 *
 * Everything that can raise an alert (the manual simulator today, an ML /
 * anomaly-detection backend later) produces a `TriggerAlertInput` and hands it
 * to `triggerAlert(...)` in `src/services/alertStore.ts`.
 */

export type Severity = 'low' | 'medium' | 'high' | 'critical';

export type AlertType =
  | 'equipment_failure'
  | 'temperature_anomaly'
  | 'conveyor_blockage'
  | 'quality_defect'
  | 'safety_hazard'
  | 'electrical_fault'
  | 'material_shortage'
  | 'sensor_failure';

/** Where an alert came from. Shown in the details drawer as "Detection". */
export type AlertSource = 'simulation' | 'ml' | 'external';

export interface SensorReading {
  /** Signal name, e.g. "Motor current". */
  label: string;
  /** Formatted value including unit, e.g. "42 A". */
  value: string;
  /** Formatted expected range, e.g. "18–30 A". */
  expected?: string;
  /** True when the value is outside the expected window. */
  outOfRange: boolean;
}

export interface FactoryAlert {
  id: string;
  zoneId: string;
  type: AlertType;
  severity: Severity;
  timestamp: Date;
  acknowledged: boolean;
  acknowledgedAt: Date | null;
  equipmentId: string;
  description: string;
  source: AlertSource;
  /** 0–1 model confidence (fictional for simulated alerts). */
  confidence: number;
  sensorData: SensorReading[];
  suggestedAction: string;
}

/**
 * The single input contract for raising an alert.
 * Only `zoneId`, `type` and `severity` are required — everything else is
 * generated from the alert-type templates when omitted.
 */
export interface TriggerAlertInput {
  zoneId: string;
  type: AlertType;
  severity: Severity;
  equipmentId?: string;
  description?: string;
  confidence?: number;
  sensorData?: SensorReading[];
  suggestedAction?: string;
  source?: AlertSource;
  timestamp?: Date;
}
