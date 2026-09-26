// The Giga site plan: zones (from gigafactory-monitor/src/config/factoryZones.ts, plus the buildings this
// prototype uses), our line inside General Assembly, the flows between buildings, and locate(): turn a
// problem (station, equipment, culprit) into a place on the map.
// Coordinates are map units = pixels of the site plan image (1448 x 1086).

export const MAP_W = 1448, MAP_H = 1086;
export const MAP_IMG = "/img/factory-map.jpg";

const z = (id, code, name, hall, polygon, cx, cy, assets = [], extra = {}) =>
  ({ id, code, name, hall, polygon, c: { x: cx, y: cy }, assets, ...extra });

export const ZONES = [
  // North hall (A1xx) - our line runs in General Assembly
  z("a108-plastics", "A108", "Plastics", "North Hall", "238,150 405,150 405,227 238,227", 321.5, 188.5, ["Injection Molding Cell PL-IM06", "Bumper Paint Robot PL-R11", "Vision Gauge PL-VG1"]),
  z("a103-body-in-white", "A103", "Body in White", "North Hall", "405,150 571,150 571,227 405,227", 488, 188.5, ["Robot Cell BIW-R17", "Spot Weld Line BIW-W04", "Framing Station BIW-F02"]),
  z("a101-stamping", "A101", "Stamping", "North Hall", "571,150 700,150 713,163 713,227 571,227", 641.5, 188.8, ["Tandem Press Line ST-P01", "Transfer Feeder ST-TF2"]),
  z("a105-seats", "A105", "Seats", "North Hall", "238,227 386,227 386,287 238,287", 312, 257, ["Seat Assembly Line SE-L01", "Seat Test Rig SE-T01"]),
  z("a104-paint", "A104", "Paint", "North Hall", "386,227 571,227 571,287 386,287", 478.5, 257, ["Paint Booth PT-B03", "Curing Oven PT-OV2"]),
  z("a102-casting", "A102", "Casting", "North Hall", "571,227 713,227 713,325 571,325", 642, 276, ["Giga Press GP-02", "Die Cooling Unit GC-CU1"]),
  z("a109-general-assembly", "A109", "General Assembly", "North Hall", "238,287 571,287 571,325 713,325 713,363 238,363", 446.2, 328.3,
    ["ST011 Marriage", "ST012 Front subframe bolt-down (NR-012, FX-012, SC-012)", "ST013 Coolant fill & leak test (CF-013, LT-013, SC-013)", "ST014 Wheel alignment & EOL"], { ours: true }),
  z("a100-storage-logistics", "A100", "Storage & Logistics", "North Hall", "238,363 713,363 713,509 238,509", 475.5, 436,
    ["High-bay rack LOG-HB04 (bolt batches)", "Sequencing line LOG-SQ02", "Coolant store LOG-CL1", "Tugger train LOG-TT3"]),
  // South hall (A0xx)
  z("a001-stamping", "A001", "Stamping", "South Hall", "238,560 365,560 365,627 238,627", 301.5, 593.5),
  z("a004-paint", "A004", "Paint", "South Hall", "365,560 440,560 440,708 365,708", 402.5, 634),
  z("a003-body-in-white", "A003", "Body in White", "South Hall", "238,627 365,627 365,747 238,747", 301.5, 687),
  z("a008-plastics", "A008", "Plastics", "South Hall", "365,708 440,708 440,762 365,762", 402.5, 735),
  z("a002-casting", "A002", "Casting", "South Hall", "365,762 440,762 440,808 365,808", 402.5, 785),
  z("a009-general-assembly", "A009", "General Assembly", "South Hall", "238,747 365,747 365,888 238,888", 301.5, 817.5),
  z("a005-seats", "A005", "Seats", "South Hall", "365,808 440,808 440,888 365,888", 402.5, 848),
  // Powertrain
  z("a106-drive-unit-expansion", "A106", "Drive Unit Expansion", "Powertrain", "537,643 641,643 641,732 537,732", 589, 687.5),
  z("a007-battery-pack", "A007", "Battery Pack", "Powertrain", "537,732 641,732 641,812 537,812", 589, 772),
  z("a006-drive-unit", "A006", "Drive Unit", "Powertrain", "537,812 641,812 641,850 537,850", 589, 831),
  z("a120-battery-cells", "A120", "Battery Cells", "Powertrain", "647,643 728,643 728,773 647,773", 687.5, 708),
  // Logistics
  z("ln-new-vehicle-logistics", "LN", "New Vehicle Logistics", "Logistics", "541,567 697,567 697,613 541,613", 619, 590,
    ["Quarantine lane LN-Q1 (cars on hold)", "Car Carrier Dock LN-D02", "Vehicle Tracking Gate LN-G1"]),
  z("lf1-logistics-yard", "LF-1", "Logistics Yard", "Logistics", "986,280 1118,280 1118,476 1102,492 986,492", 1051.7, 385.5, ["Trailer Yard Gate LF1-G2", "Container Crane LF1-CR1"]),
  // Utilities
  z("mp-material-testing", "MP", "Material Testing / Quality Lab", "Utilities", "754,630 799,630 799,702 754,702", 776.5, 666, ["CMM Gauge MP-CMM1", "Leak test master MP-LT1", "Torque audit bench MP-TA2"]),
  z("utility-energy-center", "GRA", "Energy Center", "Utilities", "754,247 856,247 856,375 754,375", 805, 311, ["Gas Turbine GKA-T1", "Substation Transformer UW2-TR2"]),
  z("vb-water-basins", "VB", "Infiltration Basins", "Utilities", "892,236 964,236 974,246 974,378 964,386 892,386 882,378 882,246", 928, 311),
  z("pwr1-process-water", "PWR-1", "Process Water Recycling", "Utilities", "754,126 880,126 880,178 754,178", 817, 152),
  z("utility-pump-substation", "PW", "Pumping & Substation", "Utilities", "748,434 846,434 846,511 748,511", 797, 472.5),
  // Added for this prototype (unlabelled buildings on the plan)
  z("df-it-mes", "DF", "IT & MES Data Center", "IT", "754,563 800,563 800,611 754,611", 777, 587,
    ["MES server MES-01", "Line network switch NET-GA1", "VIN scan gateway SCN-GW"], { added: true, tag: [777, 556, "IT · MES"] }),
  z("ed-engineering", "ED", "Engineering & Change Office", "Admin", "1132,231 1257,231 1257,292 1132,292", 1194, 261,
    ["Process engineering (work instructions)", "Change board", "Quality engineering"], { added: true, tag: [1194, 224, "Engineering"] }),
  z("wo-workshop", "WO", "Maintenance Workshop", "Maintenance", "577,864 628,864 628,906 577,906", 602, 885,
    ["Tool crib (spare sockets, seals)", "Calibration bench CAL-02", "Maintenance crew"], { added: true, tag: [602, 857, "Workshop"] }),
];
export const ZONE = Object.fromEntries(ZONES.map((x) => [x.id, x]));

// ---- our line inside General Assembly (A109) --------------------------------------------------
export const CONVEYOR = "M250,322 L560,322 C572,322 574,344 590,344 L704,344";
export const STATIONS = {
  st011: { code: "ST011", name: "Marriage", x: 300, y: 322, ours: false },
  st012: { code: "ST012", name: "Subframe bolt-down", x: 385, y: 322, ours: true, severity: 9 },
  st013: { code: "ST013", name: "Coolant fill & leak test", x: 470, y: 322, ours: true, severity: 8 },
  st014: { code: "ST014", name: "Alignment & EOL", x: 645, y: 344, ours: false },
};
export const EQUIP = {
  "NR-012": { x: 372, y: 310, st: "st012", name: "Nutrunner NR-012", kind: "tool" },
  "FX-012": { x: 385, y: 334, st: "st012", name: "Subframe fixture FX-012", kind: "fixture" },
  "SC-012": { x: 399, y: 310, st: "st012", name: "VIN scanner SC-012", kind: "scanner" },
  "CF-013": { x: 457, y: 310, st: "st013", name: "Fill head / pump CF-013", kind: "tool" },
  "LT-013": { x: 483, y: 310, st: "st013", name: "Leak tester LT-013", kind: "tester" },
  "SC-013": { x: 470, y: 334, st: "st013", name: "VIN scanner SC-013", kind: "scanner" },
};
export const OPERATOR = { st012: { x: 385, y: 350 }, st013: { x: 470, y: 350 } };
// where on a station each kind of problem is pinned (offset from the station centre, map units)
const SPOT = { wi: [-24, 3], material: [21, 13], operator: [0, 28], notes: [30, 29], flag: [26, -2], line: [0, 0] };
const spot = (S, k) => ({ x: S.x + SPOT[k][0], y: S.y + SPOT[k][1] });
export const PLACES = {
  hb: { x: 330, y: 468, label: "High-bay rack LOG-HB04", zone: "a100-storage-logistics" },
  sq: { x: 470, y: 402, label: "Sequencing line LOG-SQ02", zone: "a100-storage-logistics" },
  coolant: { x: 600, y: 470, label: "Coolant store LOG-CL1", zone: "a100-storage-logistics" },
  mes: { x: 777, y: 587, label: "MES server room (DF)", zone: "df-it-mes" },
  eng: { x: 1194, y: 261, label: "Engineering & change office (ED)", zone: "ed-engineering" },
  shop: { x: 602, y: 885, label: "Maintenance workshop (WO)", zone: "wo-workshop" },
  lab: { x: 776, y: 666, label: "Quality lab (MP)", zone: "mp-material-testing" },
  yard: { x: 619, y: 590, label: "Quarantine lane LN-Q1", zone: "ln-new-vehicle-logistics" },
  gate: { x: 150, y: 540, label: "Main gate - shipped", zone: null },
};

// ---- flows between buildings (drawn as animated routes) ------------------------------------------
export const FLOWS = [
  { id: "bolts", kind: "material", label: "bolt batches → ST012", d: "M330,468 C330,420 385,410 385,352" },
  { id: "coolant", kind: "material", label: "coolant → ST013", d: "M600,470 C600,420 470,420 470,352" },
  { id: "cars", kind: "cars", label: "finished cars → yard", d: "M704,344 L733,344 L733,548 Q733,558 720,558 L630,558 L630,570" },
  { id: "ship", kind: "cars", label: "released cars → customers", d: "M560,590 L522,590 L522,545 L215,545 L150,540" },
  { id: "data", kind: "data", label: "cycle data → MES", d: "M470,337 C520,380 745,420 745,500 L760,570" },
  { id: "wi", kind: "method", label: "work instructions → line", d: "M1132,262 C1000,262 900,210 760,215 C720,216 700,290 600,300 L400,305" },
  { id: "maint", kind: "maint", label: "technicians → line", d: "M602,864 L602,820 L720,820 L740,560 L740,380 L520,356" },
];

// ---- where did it happen? ---------------------------------------------------------------------------
const EQ_RE = /\b(NR|FX|SC|CF|LT)-01[23]\b/;
export function equipOf(text, station) {
  const m = String(text || "").match(EQ_RE);
  if (m) return m[0];
  const t = String(text || "").toLowerCase();
  const s = station === "st013" ? "013" : "012";
  if (/scanner|vin|e-sc/.test(t)) return `SC-${s}`;
  if (/nutrunner|socket|e-nr/.test(t)) return "NR-012";
  if (/fill|seal|e-cf|vacuum|nozzle/.test(t)) return "CF-013";
  if (/leak/.test(t) && s === "013") return "LT-013";
  if (/fixture|clamp/.test(t)) return "FX-012";
  return null;
}

/**
 * -> { x, y, st, equip, cluster, path: [breadcrumbs], from?: {x, y, label} }
 * cluster: where the item sits when the map is zoomed out (one badge per station / building).
 */
export function locate(it) {
  const st = STATIONS[it.station] ? it.station : "st012";
  const S = STATIONS[st];
  const hall = ["North Hall", "A109 General Assembly", `${S.code} ${S.name}`];
  const base = { st, cluster: st, path: hall };
  const culprit = String(it.culprit || "");
  if (it.kind === "cars") {
    const p = PLACES[it.place];
    const zz = p.zone ? ZONE[p.zone] : null;
    return { x: p.x, y: p.y, cluster: it.place, path: zz ? [zz.hall, `${zz.code} ${zz.name}`, p.label] : [p.label], st: null };
  }
  if (it.kind === "method") return { ...base, ...spot(S, "wi"), path: [...hall, `WI board - ${it.wi || culprit}`], from: PLACES.eng };
  if (it.kind === "note") return { ...base, ...spot(S, "notes"), path: [...hall, "floor notes"] };
  if (it.kind === "maint" || it.kind === "repair" || it.cause === "machine" || it.kind === "flag") {
    const eq = equipOf(`${culprit} ${it.equipment || ""} ${it.title || ""}`, st);
    if (eq && EQUIP[eq]) {
      const E = EQUIP[eq];
      const out = { ...base, st: E.st, cluster: E.st, x: E.x, y: E.y, equip: eq,
        path: ["North Hall", "A109 General Assembly", `${STATIONS[E.st].code} ${STATIONS[E.st].name}`, E.name] };
      if (it.kind === "maint" || it.kind === "repair" || it.cause === "machine") out.from = PLACES.shop;
      return out;
    }
    if (it.kind === "flag") return { ...base, ...spot(S, "flag") };
  }
  if (it.cause === "people") return { ...base, ...spot(S, "operator"), path: [...hall, `operator ${culprit}`] };
  if (it.cause === "method") return { ...base, ...spot(S, "wi"), path: [...hall, `WI board - ${culprit}`], from: PLACES.eng };
  if (it.cause === "station") {
    if (/batch/i.test(culprit)) {
      const src = /coolant|CL-/i.test(culprit) ? PLACES.coolant : PLACES.hb;
      return { ...base, ...spot(S, "material"), path: [...hall, `parts rack - ${culprit}`], from: { ...src, label: `${src.label} - ${culprit}` } };
    }
    if (/supply/i.test(culprit)) return { ...base, ...spot(S, "material"), path: [...hall, "parts rack - material supply"], from: PLACES.sq };
    if (/MES|network/i.test(culprit)) {
      const eq = `SC-${st === "st013" ? "013" : "012"}`;
      return { ...base, x: EQUIP[eq].x, y: EQUIP[eq].y, equip: eq, path: [...hall, EQUIP[eq].name], from: PLACES.mes };
    }
  }
  if (it.cause === "machine") return { ...base, x: S.x, y: S.y - 12, from: PLACES.shop };
  return { ...base, ...spot(S, "flag") };
}

export const CAUSE_COLOR = { machine: "var(--c-machine)", people: "var(--c-people)", method: "var(--c-method)",
  station: "var(--c-station)", unclear: "var(--c-unclear)" };
