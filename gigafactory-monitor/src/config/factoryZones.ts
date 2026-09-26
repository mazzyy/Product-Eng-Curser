import type { FactoryZone } from '../types/zones';

/**
 * ============================================================================
 *  FACTORY ZONE CONFIGURATION
 * ============================================================================
 *
 * The SVG overlay uses the same coordinate system as the site-plan image:
 * one map unit = one pixel of the ORIGINAL image (1448 × 1086 px).
 *
 *  - If you replace /public/factory-map.jpg with a higher-resolution export of
 *    the SAME drawing (same aspect ratio), keep MAP_WIDTH / MAP_HEIGHT as they
 *    are — the image is stretched onto this coordinate system, so every
 *    polygon below stays aligned.
 *  - If you use a different drawing, set MAP_WIDTH / MAP_HEIGHT to its pixel
 *    size and redraw the polygons (the "Edit zones" tool in the map's
 *    bottom-left corner shows cursor coordinates and builds polygon strings).
 *
 * Building codes (A1xx north hall, A0xx south hall) come from the Giga Berlin
 * permit plan. They follow one scheme per hall: x01 Stamping, x02 Casting,
 * x03 Body in White, x04 Paint, x05 Seats, x06 Drive Unit, x07 Battery Pack,
 * x08 Plastics, x09 General Assembly. Utility codes are the labels printed on
 * the map.
 *
 * Polygon format: "x1,y1 x2,y2 x3,y3 ..." (clockwise or counter-clockwise).
 * Centroid: where markers are placed and where the camera zooms to.
 */

export const MAP_IMAGE_URL = '/factory-map.jpg';
export const MAP_WIDTH = 1448;
export const MAP_HEIGHT = 1086;

export const FACTORY_ZONES: FactoryZone[] = [
  // ── North hall (A1xx) ───────────────────────────────────────────────────────
  {
    id: 'a108-plastics',
    code: 'A108',
    name: 'Plastics',
    hall: 'North Hall',
    polygon: '238,150 405,150 405,227 238,227',
    centroid: { x: 321.5, y: 188.5 },
    assets: [
      'Injection Molding Cell PL-IM06',
      'Bumper Paint Robot PL-R11',
      'Mold Temperature Unit PL-MT3',
      'Part Conveyor PL-C02',
      'Vision Gauge PL-VG1',
    ],
  },
  {
    id: 'a103-body-in-white',
    code: 'A103',
    name: 'Body in White',
    hall: 'North Hall',
    polygon: '405,150 571,150 571,227 405,227',
    centroid: { x: 488, y: 188.5 },
    assets: [
      'Robot Cell BIW-R17',
      'Spot Weld Line BIW-W04',
      'Framing Station BIW-F02',
      'Body Conveyor BIW-C09',
      'Vision Gauge BIW-VG3',
    ],
  },
  {
    id: 'a101-stamping',
    code: 'A101',
    name: 'Stamping',
    hall: 'North Hall',
    polygon: '571,150 700,150 713,163 713,227 571,227',
    centroid: { x: 641.5, y: 188.8 },
    assets: [
      'Tandem Press Line ST-P01',
      'Transfer Feeder ST-TF2',
      'Blank Destacker ST-BD1',
      'Die Change Cart ST-DC4',
      'Scrap Conveyor ST-C03',
    ],
  },
  {
    id: 'a105-seats',
    code: 'A105',
    name: 'Seats',
    hall: 'North Hall',
    polygon: '238,227 386,227 386,287 238,287',
    centroid: { x: 312, y: 257 },
    assets: [
      'Seat Assembly Line SE-L01',
      'Foam Molding Cell SE-FM2',
      'Seat Conveyor SE-C03',
      'Seat Test Rig SE-T01',
    ],
  },
  {
    id: 'a104-paint',
    code: 'A104',
    name: 'Paint',
    hall: 'North Hall',
    polygon: '386,227 571,227 571,287 386,287',
    centroid: { x: 478.5, y: 257 },
    assets: [
      'Paint Booth PT-B03',
      'Paint Robot PT-R08',
      'Curing Oven PT-OV2',
      'E-Coat Tank PT-EC1',
      'Booth Air Supply PT-HVAC2',
    ],
  },
  {
    id: 'a102-casting',
    code: 'A102',
    name: 'Casting',
    hall: 'North Hall',
    polygon: '571,227 713,227 713,325 571,325',
    centroid: { x: 642, y: 276 },
    assets: [
      'Giga Press GP-02',
      'Die Cooling Unit GC-CU1',
      'Melting Furnace GC-MF3',
      'Trim Press GC-TP2',
      'X-Ray Inspection GC-XR2',
    ],
  },
  {
    id: 'a109-general-assembly',
    code: 'A109',
    name: 'General Assembly',
    hall: 'North Hall',
    polygon: '238,287 571,287 571,325 713,325 713,363 238,363',
    centroid: { x: 446.2, y: 328.3 },
    assets: [
      'Final Assembly Line GA-L02',
      'Torque Station GA-T14',
      'Glass Install Robot GA-R05',
      'Marriage Station GA-MS1',
      'Overhead Conveyor GA-C07',
      'EOL Test Rig GA-EOL2',
    ],
  },
  {
    id: 'a100-storage-logistics',
    code: 'A100',
    name: 'Storage & Logistics / Future Production',
    shortName: 'Storage & Logistics',
    hall: 'North Hall',
    polygon: '238,363 713,363 713,509 238,509',
    centroid: { x: 475.5, y: 436 },
    assets: [
      'Sequencing Line LOG-SQ02',
      'AGV Fleet LOG-AGV12',
      'High-Bay Rack LOG-HB04',
      'Tugger Train LOG-TT3',
      'Dock Door Conveyor LOG-D17',
    ],
  },

  // ── South hall (A0xx) ───────────────────────────────────────────────────────
  {
    id: 'a001-stamping',
    code: 'A001',
    name: 'Stamping',
    hall: 'South Hall',
    polygon: '238,560 365,560 365,627 238,627',
    centroid: { x: 301.5, y: 593.5 },
    assets: [
      'Press Line ST-S-P01',
      'Transfer Feeder ST-S-TF1',
      'Blank Destacker ST-S-BD1',
      'Die Change Cart ST-S-DC2',
    ],
  },
  {
    id: 'a004-paint',
    code: 'A004',
    name: 'Paint',
    hall: 'South Hall',
    polygon: '365,560 440,560 440,708 365,708',
    centroid: { x: 402.5, y: 634 },
    assets: [
      'Paint Booth PT-S-B01',
      'Paint Robot PT-S-R04',
      'Curing Oven PT-S-OV1',
      'E-Coat Tank PT-S-EC1',
    ],
  },
  {
    id: 'a003-body-in-white',
    code: 'A003',
    name: 'Body in White',
    hall: 'South Hall',
    polygon: '238,627 365,627 365,747 238,747',
    centroid: { x: 301.5, y: 687 },
    assets: [
      'Robot Cell BIW-S-R22',
      'Laser Braze Cell BIW-S-LB1',
      'Underbody Line BIW-S-U03',
      'Body Conveyor BIW-S-C05',
      'Vision Gauge BIW-S-VG2',
    ],
  },
  {
    id: 'a008-plastics',
    code: 'A008',
    name: 'Plastics',
    hall: 'South Hall',
    polygon: '365,708 440,708 440,762 365,762',
    centroid: { x: 402.5, y: 735 },
    assets: [
      'Injection Molding Cell PL-S-IM02',
      'Mold Temperature Unit PL-S-MT1',
      'Part Conveyor PL-S-C01',
    ],
  },
  {
    id: 'a002-casting',
    code: 'A002',
    name: 'Casting',
    hall: 'South Hall',
    polygon: '365,762 440,762 440,808 365,808',
    centroid: { x: 402.5, y: 785 },
    assets: [
      'Giga Press GP-S01',
      'Die Cooling Unit GC-S-CU2',
      'Holding Furnace GC-S-HF1',
      'X-Ray Inspection GC-S-XR1',
    ],
  },
  {
    id: 'a009-general-assembly',
    code: 'A009',
    name: 'General Assembly',
    hall: 'South Hall',
    polygon: '238,747 365,747 365,888 238,888',
    centroid: { x: 301.5, y: 817.5 },
    assets: [
      'Final Assembly Line GA-S-L01',
      'Wheel Alignment Station GA-S-WA2',
      'Fluid Fill Station GA-S-FF3',
      'Torque Station GA-S-T06',
      'EOL Test Rig GA-S-EOL1',
    ],
  },
  {
    id: 'a005-seats',
    code: 'A005',
    name: 'Seats',
    hall: 'South Hall',
    polygon: '365,808 440,808 440,888 365,888',
    centroid: { x: 402.5, y: 848 },
    assets: [
      'Seat Assembly Line SE-S-L01',
      'Seat Conveyor SE-S-C02',
      'Seat Test Rig SE-S-T01',
    ],
  },

  // ── Powertrain block ────────────────────────────────────────────────────────
  {
    id: 'a106-drive-unit-expansion',
    code: 'A106',
    name: 'Drive Unit Expansion',
    shortName: 'Drive Unit Exp.',
    hall: 'Powertrain',
    polygon: '537,643 641,643 641,732 537,732',
    centroid: { x: 589, y: 687.5 },
    assets: [
      'Rotor Assembly Cell DU-RA2',
      'Stator Winding Line DU-SW1',
      'Gear Test Bench DU-GT4',
      'Inverter Line DU-IN3',
      'Part Conveyor DU-C02',
    ],
  },
  {
    id: 'a007-battery-pack',
    code: 'A007 / A107',
    name: 'Battery Pack',
    hall: 'Powertrain',
    polygon: '537,732 641,732 641,812 537,812',
    centroid: { x: 589, y: 772 },
    assets: [
      'Module Assembly Line BP-L01',
      'Cell Stacking Robot BP-R06',
      'Pack Leak Test Station BP-LT2',
      'Pack Conveyor BP-C03',
    ],
  },
  {
    id: 'a006-drive-unit',
    code: 'A006',
    name: 'Drive Unit',
    hall: 'Powertrain',
    polygon: '537,812 641,812 641,850 537,850',
    centroid: { x: 589, y: 831 },
    assets: [
      'Rotor Assembly Cell DU-S-RA1',
      'Stator Winding Line DU-S-SW1',
      'Gear Test Bench DU-S-GT2',
      'Inverter Line DU-S-IN1',
    ],
  },
  {
    id: 'a120-battery-cells',
    code: 'A120',
    name: 'Battery Cells',
    hall: 'Powertrain',
    polygon: '647,643 728,643 728,773 647,773',
    centroid: { x: 687.5, y: 708 },
    assets: [
      'Electrode Coater BC-EC1',
      'Calendering Line BC-CL2',
      'Formation Rack BC-FR07',
      'Dry Room HVAC BC-DR1',
      'Cell Transfer Conveyor BC-C04',
    ],
  },

  // ── Logistics ───────────────────────────────────────────────────────────────
  {
    id: 'ln-new-vehicle-logistics',
    code: 'LN',
    name: 'New Vehicle Logistics',
    hall: 'Logistics',
    polygon: '541,567 697,567 697,613 541,613',
    centroid: { x: 619, y: 590 },
    assets: [
      'Car Carrier Dock LN-D02',
      'Vehicle Tracking Gate LN-G1',
      'Shuttle Tugger LN-TT2',
      'Yard Lighting Feeder LN-EL1',
    ],
  },
  {
    id: 'lf1-logistics-yard',
    code: 'LF-1',
    name: 'Logistics Yard',
    hall: 'Logistics',
    polygon: '986,280 1118,280 1118,476 1102,492 986,492',
    centroid: { x: 1051.7, y: 385.5 },
    assets: [
      'Trailer Yard Gate LF1-G2',
      'Tugger Train LF1-TT4',
      'Container Crane LF1-CR1',
      'Dock Door Conveyor LF1-D08',
    ],
  },

  // ── Utilities ───────────────────────────────────────────────────────────────
  {
    id: 'mp-material-testing',
    code: 'MP',
    name: 'Material Testing',
    hall: 'Utilities',
    polygon: '754,630 799,630 799,702 754,702',
    centroid: { x: 776.5, y: 666 },
    assets: [
      'CMM Gauge MP-CMM1',
      'X-Ray CT Inspection MP-CT2',
      'Climate Chamber MP-CC3',
      'Tensile Test Bench MP-TT1',
    ],
  },
  {
    id: 'utility-energy-center',
    code: 'GRA / UWF-2',
    name: 'Energy Center',
    hall: 'Utilities',
    polygon: '754,247 856,247 856,375 754,375',
    centroid: { x: 805, y: 311 },
    assets: [
      'Gas Turbine GKA-T1',
      'Substation Transformer UW2-TR2',
      'Battery Storage BS-02',
      'Chiller Plant UT-CH3',
      'Feeder Switchgear UW2-SG4',
    ],
  },
  {
    id: 'vb-water-basins',
    code: 'VB',
    name: 'Infiltration Basins',
    hall: 'Utilities',
    polygon: '892,236 964,236 974,246 974,378 964,386 892,386 882,378 882,246',
    centroid: { x: 928, y: 311.1 },
    assets: [
      'Inflow Pump VB-P1',
      'Level Sensor VB-LS2',
      'Water Quality Probe VB-QP1',
      'Overflow Gate VB-OG1',
    ],
  },
  {
    id: 'pwr1-process-water',
    code: 'PWR-1',
    name: 'Process Water Recycling',
    hall: 'Utilities',
    polygon: '754,126 880,126 880,178 754,178',
    centroid: { x: 817, y: 152 },
    assets: [
      'Process Water Tank PWR-T1',
      'Reverse Osmosis Unit PWR-RO2',
      'Recirculation Pump PWR-P3',
      'Conductivity Probe PWR-QP1',
    ],
  },
  {
    id: 'utility-pump-substation',
    code: 'PW / UW / PWR',
    name: 'Pumping & Substation',
    hall: 'Utilities',
    polygon: '748,434 846,434 846,511 748,511',
    centroid: { x: 797, y: 472.5 },
    assets: [
      'Pumping Station PW-P2',
      'Substation Transformer UW-TR1',
      'Process Water Filter PWR-F3',
      'Feeder Switchgear UW-SG2',
    ],
  },
];

const ZONE_INDEX: Record<string, FactoryZone> = Object.fromEntries(
  FACTORY_ZONES.map((zone) => [zone.id, zone]),
);

export function getZone(zoneId: string): FactoryZone | undefined {
  return ZONE_INDEX[zoneId];
}

/** Short display label, e.g. "A103 Body in White". */
export function zoneLabel(zone: FactoryZone): string {
  return `${zone.code} ${zone.shortName ?? zone.name}`;
}

/** Zones grouped by hall, in configuration order (for dropdowns). */
export function zonesByHall(): Array<{ hall: string; zones: FactoryZone[] }> {
  const groups: Array<{ hall: string; zones: FactoryZone[] }> = [];
  for (const zone of FACTORY_ZONES) {
    let group = groups.find((g) => g.hall === zone.hall);
    if (!group) {
      group = { hall: zone.hall, zones: [] };
      groups.push(group);
    }
    group.zones.push(zone);
  }
  return groups;
}
