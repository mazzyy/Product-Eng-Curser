import type { TriggerAlertInput } from '../types/alerts';

/**
 * Scripted demo scenarios. Each step is sent through triggerAlert(...) exactly
 * like a manual or backend alert, after `delayMs` from the start.
 */
export interface ScenarioStep {
  delayMs: number;
  alert: TriggerAlertInput;
}

export interface DemoScenario {
  id: string;
  title: string;
  summary: string;
  steps: ScenarioStep[];
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: 'scenario-1',
    title: 'Scenario 1 — Single Equipment Failure',
    summary: 'HIGH equipment failure in Body in White (A103).',
    steps: [
      {
        delayMs: 0,
        alert: {
          zoneId: 'a103-body-in-white',
          type: 'equipment_failure',
          severity: 'high',
          equipmentId: 'Robot Cell BIW-R17',
          description: 'Torque threshold exceeded on axis 4 during weld cycle',
          confidence: 0.94,
          sensorData: [
            { label: 'Motor current', value: '42 A', expected: '18–30 A', outOfRange: true },
            { label: 'Axis 4 torque', value: '112 %', expected: '40–85 %', outOfRange: true },
            { label: 'Drive temperature', value: '84 °C', expected: '35–70 °C', outOfRange: true },
            { label: 'Cycle time', value: '61.8 s', expected: '52–56 s', outOfRange: true },
          ],
          suggestedAction:
            'Inspect robot cell and associated motor drive. Check axis 4 gearbox and brake before restarting.',
        },
      },
    ],
  },
  {
    id: 'scenario-2',
    title: 'Scenario 2 — Quality + Logistics',
    summary: 'MEDIUM quality alert in General Assembly, then HIGH material shortage in Storage & Logistics.',
    steps: [
      {
        delayMs: 0,
        alert: {
          zoneId: 'a109-general-assembly',
          type: 'quality_defect',
          severity: 'medium',
          equipmentId: 'Torque Station GA-T14',
          description: 'Door gap deviation on 4 of the last 20 bodies',
          confidence: 0.88,
          sensorData: [
            { label: 'Gap deviation (door RR)', value: '1.2 mm', expected: '0–0.5 mm', outOfRange: true },
            { label: 'Defect rate', value: '3.8 %', expected: '0–1.5 %', outOfRange: true },
            { label: 'First-pass yield', value: '91.4 %', expected: '96–100 %', outOfRange: true },
          ],
          suggestedAction:
            'Hold affected bodies at the quality gate and check door hinge fixture and bolt torque on GA-T14.',
        },
      },
      {
        delayMs: 1600,
        alert: {
          zoneId: 'a100-storage-logistics',
          type: 'material_shortage',
          severity: 'high',
          equipmentId: 'Sequencing Line LOG-SQ02',
          description: 'Sequenced seat delivery to General Assembly delayed — stock below safety level',
          confidence: 0.91,
          sensorData: [
            { label: 'Stock coverage (seats)', value: '6 min', expected: '30–120 min', outOfRange: true },
            { label: 'Delivery delay', value: '18 min', expected: '0–5 min', outOfRange: true },
            { label: 'Open kanban cards', value: '11 pcs', expected: '0–4 pcs', outOfRange: true },
          ],
          suggestedAction:
            'Expedite seat sequence from the high-bay store and warn GA line supervisor of possible line stop.',
        },
      },
    ],
  },
  {
    id: 'scenario-3',
    title: 'Scenario 3 — Critical Production Incident',
    summary: 'MEDIUM sensor warning, then CRITICAL Giga Press failure in Casting (A002).',
    steps: [
      {
        delayMs: 0,
        alert: {
          zoneId: 'a002-casting',
          type: 'sensor_failure',
          severity: 'medium',
          equipmentId: 'Die Cooling Unit GC-S-CU2',
          description: 'Thermocouple TC-07 signal implausible — value frozen',
          confidence: 0.86,
          sensorData: [
            { label: 'TC-07 signal', value: 'Frozen at 212 °C', expected: 'Live', outOfRange: true },
            { label: 'Redundant channel TC-08', value: '247 °C', expected: '±2 % of TC-07', outOfRange: true },
            { label: 'Coolant flow', value: '131 L/min', expected: '120–160 L/min', outOfRange: false },
          ],
          suggestedAction: 'Switch die temperature control to TC-08 and check TC-07 wiring at the next die change.',
        },
      },
      {
        delayMs: 1800,
        alert: {
          zoneId: 'a002-casting',
          type: 'equipment_failure',
          severity: 'critical',
          equipmentId: 'Giga Press GP-S01',
          description: 'Hydraulic pressure drop during shot — press halted',
          confidence: 0.96,
          sensorData: [
            { label: 'Hydraulic pressure', value: '142 bar', expected: '180–210 bar', outOfRange: true },
            { label: 'Die temperature', value: '318 °C', expected: '220–280 °C', outOfRange: true },
            { label: 'Accumulator charge', value: '61 %', expected: '85–100 %', outOfRange: true },
            { label: 'Shot cycle', value: 'Aborted (F-3102)', expected: 'Complete', outOfRange: true },
          ],
          suggestedAction:
            'Keep the casting cell stopped, secure the die area and inspect hydraulic accumulator, valves and lines.',
        },
      },
    ],
  },
];
