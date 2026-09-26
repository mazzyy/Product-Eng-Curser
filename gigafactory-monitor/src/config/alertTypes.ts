import type { AlertType } from '../types/alerts';

/**
 * ============================================================================
 *  ALERT TYPE CATALOGUE
 * ============================================================================
 *
 * To add a new alert type:
 *   1. Add its id to the `AlertType` union in src/types/alerts.ts
 *   2. Add an entry to ALERT_TYPES below (TypeScript will tell you if one is
 *      missing because the object is typed as Record<AlertType, ...>)
 *   3. (Optional) Add it to ALERT_TYPE_ORDER to control dropdown position.
 *
 * The templates here are only used to generate fictional details for
 * simulated alerts. A real backend can send its own description, sensor data
 * and suggested action with the event instead.
 */

/** A numeric signal. The simulated value is drawn from `anomaly`. */
export interface NumericSensorTemplate {
  kind: 'numeric';
  label: string;
  unit: string;
  /** Expected (normal) range. */
  min: number;
  max: number;
  /** Range the anomalous value is drawn from (above OR below the normal range). */
  anomaly: [number, number];
  decimals?: number;
}

/** A discrete / textual signal (states, codes). */
export interface TextSensorTemplate {
  kind: 'text';
  label: string;
  value: string;
  expected?: string;
  outOfRange: boolean;
}

export type SensorTemplate = NumericSensorTemplate | TextSensorTemplate;

export interface AlertTypeDefinition {
  id: AlertType;
  /** Label shown in dropdowns, cards and lists. */
  label: string;
  /** Three-letter code used in compact places. */
  code: string;
  /** Category shown in the details drawer. */
  category: string;
  /**
   * Used to pick plausible equipment from the zone's asset list when the event
   * does not name one (matched against the asset name).
   */
  equipmentHint: RegExp;
  descriptions: string[];
  sensors: SensorTemplate[];
  suggestedActions: string[];
}

export const ALERT_TYPES: Record<AlertType, AlertTypeDefinition> = {
  equipment_failure: {
    id: 'equipment_failure',
    label: 'Equipment Failure',
    code: 'EQF',
    category: 'Equipment Failure',
    equipmentHint: /robot|press|cell|station|line|turbine|rig|bench|coater|furnace/i,
    descriptions: [
      'Torque threshold exceeded on drive axis',
      'Servo drive fault — axis halted mid-cycle',
      'Abnormal vibration signature on main drive',
      'Cycle time deviation above 35 % for 10 cycles',
    ],
    sensors: [
      { kind: 'numeric', label: 'Motor current', unit: 'A', min: 18, max: 30, anomaly: [36, 46] },
      { kind: 'numeric', label: 'Drive temperature', unit: '°C', min: 35, max: 70, anomaly: [76, 92] },
      { kind: 'numeric', label: 'Vibration (RMS)', unit: 'mm/s', min: 0.5, max: 4.5, anomaly: [6.5, 11], decimals: 1 },
    ],
    suggestedActions: [
      'Inspect the equipment and its motor drive. Check axis encoder and brake before restarting the cycle.',
      'Stop the cell in a safe position, inspect the drive train and review the last fault log entries.',
    ],
  },
  temperature_anomaly: {
    id: 'temperature_anomaly',
    label: 'Temperature Anomaly',
    code: 'TMP',
    category: 'Process Temperature',
    equipmentHint: /oven|furnace|cooling|chiller|turbine|press|booth|hvac|tank|mold|storage|climate/i,
    descriptions: [
      'Temperature rising above process window',
      'Cooling circuit ΔT outside tolerance',
      'Hot spot detected by thermal camera',
    ],
    sensors: [
      { kind: 'numeric', label: 'Surface temperature', unit: '°C', min: 40, max: 65, anomaly: [74, 96] },
      { kind: 'numeric', label: 'Coolant flow', unit: 'L/min', min: 120, max: 160, anomaly: [68, 96] },
      { kind: 'numeric', label: 'Coolant return ΔT', unit: 'K', min: 4, max: 8, anomaly: [11, 16], decimals: 1 },
    ],
    suggestedActions: [
      'Verify coolant flow and heat-exchanger status; reduce cycle rate until temperature is back in window.',
      'Check the thermal camera image on site and inspect the cooling circuit for blockage or leakage.',
    ],
  },
  conveyor_blockage: {
    id: 'conveyor_blockage',
    label: 'Conveyor Blockage',
    code: 'CNV',
    category: 'Material Flow',
    equipmentHint: /conveyor|agv|tugger|sequenc|transfer|destacker/i,
    descriptions: [
      'Carrier jam detected at transfer point',
      'Line stopped — part not cleared from station',
      'Photo-eye blocked for longer than 45 s',
    ],
    sensors: [
      { kind: 'numeric', label: 'Belt speed', unit: 'm/min', min: 12, max: 18, anomaly: [0, 3], decimals: 1 },
      { kind: 'numeric', label: 'Drive load', unit: '%', min: 35, max: 70, anomaly: [92, 118] },
      { kind: 'text', label: 'Blocked sensor', value: 'PE-114 (transfer)', expected: 'Clear', outOfRange: true },
    ],
    suggestedActions: [
      'Lock out the conveyor section, clear the obstruction and verify photo-eye alignment before restart.',
    ],
  },
  quality_defect: {
    id: 'quality_defect',
    label: 'Quality Defect',
    code: 'QLT',
    category: 'Quality',
    equipmentHint: /vision|gauge|x-ray|inspection|test|eol|weld|braze|paint|line|station/i,
    descriptions: [
      'Dimensional deviation on 4 of last 20 parts',
      'Surface defect rate above control limit',
      'Weld spatter detected by inline vision',
    ],
    sensors: [
      { kind: 'numeric', label: 'Defect rate', unit: '%', min: 0, max: 1.5, anomaly: [2.8, 6.2], decimals: 1 },
      { kind: 'numeric', label: 'Gap deviation', unit: 'mm', min: 0, max: 0.5, anomaly: [0.8, 1.6], decimals: 2 },
      { kind: 'numeric', label: 'First-pass yield', unit: '%', min: 96, max: 100, anomaly: [84, 93], decimals: 1 },
    ],
    suggestedActions: [
      'Quarantine parts produced since the last good sample and request a quality engineering review.',
      'Pull the last 20 parts for manual measurement and check fixture wear and clamping.',
    ],
  },
  safety_hazard: {
    id: 'safety_hazard',
    label: 'Safety Hazard',
    code: 'SAF',
    category: 'Safety',
    equipmentHint: /robot|cell|press|agv|station|tugger/i,
    descriptions: [
      'Light curtain breach while cell in automatic mode',
      'Person detected inside robot envelope',
      'Emergency stop actuated at operator panel',
    ],
    sensors: [
      { kind: 'text', label: 'Light curtain', value: 'Interrupted', expected: 'Clear', outOfRange: true },
      { kind: 'text', label: 'Safety PLC state', value: 'Safe stop 1', expected: 'Run', outOfRange: true },
      { kind: 'text', label: 'Persons in zone', value: '1 detected', expected: '0', outOfRange: true },
    ],
    suggestedActions: [
      'Confirm the area is clear, investigate on site and reset only after supervisor sign-off.',
    ],
  },
  electrical_fault: {
    id: 'electrical_fault',
    label: 'Power / Electrical Fault',
    code: 'ELF',
    category: 'Power / Electrical',
    equipmentHint: /substation|switchgear|transformer|turbine|battery storage|inverter|feeder|press|line|oven/i,
    descriptions: [
      'Supply voltage sag on feeder',
      'Ground fault detected in drive cabinet',
      'Phase imbalance above tolerance',
    ],
    sensors: [
      { kind: 'numeric', label: 'Supply voltage', unit: 'V', min: 390, max: 410, anomaly: [348, 374] },
      { kind: 'numeric', label: 'Phase imbalance', unit: '%', min: 0, max: 3, anomaly: [6, 11], decimals: 1 },
      { kind: 'numeric', label: 'Cabinet temperature', unit: '°C', min: 25, max: 45, anomaly: [54, 68] },
    ],
    suggestedActions: [
      'Dispatch electrical maintenance to check the feeder and cabinet; do not reset the breaker before inspection.',
    ],
  },
  material_shortage: {
    id: 'material_shortage',
    label: 'Material Shortage',
    code: 'MAT',
    category: 'Logistics',
    equipmentHint: /sequenc|agv|tugger|rack|dock|gate|line|conveyor/i,
    descriptions: [
      'Line-side stock below safety level',
      'Sequenced parts delivery delayed',
      'Kanban replenishment missed for 2 cycles',
    ],
    sensors: [
      { kind: 'numeric', label: 'Stock coverage', unit: 'min', min: 30, max: 120, anomaly: [4, 12] },
      { kind: 'numeric', label: 'Delivery delay', unit: 'min', min: 0, max: 5, anomaly: [12, 28] },
      { kind: 'numeric', label: 'Open kanban cards', unit: 'pcs', min: 0, max: 4, anomaly: [8, 15] },
    ],
    suggestedActions: [
      'Expedite replenishment from the high-bay store and notify the line supervisor of possible starvation.',
    ],
  },
  sensor_failure: {
    id: 'sensor_failure',
    label: 'Sensor Failure',
    code: 'SNS',
    category: 'Instrumentation',
    equipmentHint: /sensor|probe|cooling|gauge|vision|x-ray|inspection|coater|rack|hvac|chiller/i,
    descriptions: [
      'Sensor signal implausible — value frozen',
      'Sensor signal lost (possible wire break)',
      'Calibration drift beyond tolerance',
    ],
    sensors: [
      { kind: 'text', label: 'Signal status', value: 'Frozen / implausible', expected: 'Live', outOfRange: true },
      { kind: 'numeric', label: 'Redundant channel deviation', unit: '%', min: 0, max: 2, anomaly: [8, 18], decimals: 1 },
      { kind: 'numeric', label: 'Signal noise', unit: '%', min: 0, max: 2, anomaly: [9, 24], decimals: 1 },
    ],
    suggestedActions: [
      'Check sensor wiring and connector, switch to the redundant channel and schedule recalibration.',
    ],
  },
};

/** Dropdown order. */
export const ALERT_TYPE_ORDER: AlertType[] = [
  'equipment_failure',
  'temperature_anomaly',
  'conveyor_blockage',
  'quality_defect',
  'safety_hazard',
  'electrical_fault',
  'material_shortage',
  'sensor_failure',
];

export const ALERT_TYPE_LIST: AlertTypeDefinition[] = ALERT_TYPE_ORDER.map((id) => ALERT_TYPES[id]);

export function isAlertType(value: unknown): value is AlertType {
  return typeof value === 'string' && value in ALERT_TYPES;
}
