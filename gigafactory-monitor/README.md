# Gigafactory Monitoring — HMI prototype

Interactive frontend prototype of a factory-monitoring tablet UI. The site plan
is shown as a zoomable map with clickable zones. Simulated alerts highlight a
zone, zoom to it, show an info card, and play a severity-specific sound.

Pure frontend: React + TypeScript + Vite, CSS, SVG overlays and the Web Audio
API. No backend, database, authentication or map services.

---

## Run it

Requirements: **Node.js 18 or newer** (includes npm).

```bash
npm install
npm run dev
```

Open the URL Vite prints (normally <http://localhost:5173>).

**On a tablet:** the dev server listens on your network (`host: true` in
`vite.config.ts`). Vite also prints a *Network* URL such as
`http://192.168.1.20:5173`. Open that URL on a tablet connected to the same
Wi-Fi. You may need to allow Node through your computer's firewall.

Other scripts:

| Command             | What it does                                   |
| ------------------- | ---------------------------------------------- |
| `npm run build`     | Production build into `dist/`                  |
| `npm run preview`   | Serve the production build locally             |
| `npm run typecheck` | Full TypeScript check (`tsc --noEmit`)         |

## The map image

The site plan is already included at **`public/factory-map.jpg`**. Vite serves
everything in `public/` from the site root, so the app loads it as
`/factory-map.jpg`.

To use a different image, overwrite that file and keep the file name. If it is
missing, the map shows a notice, and the zones still work.

The included plan is 1448 × 1086 px, and every zone coordinate is expressed in
that pixel grid. A higher-resolution export of the **same drawing with the same
aspect ratio** can replace it without any other change, because the image is
stretched onto that grid. A different drawing needs new `MAP_WIDTH` /
`MAP_HEIGHT` values and redrawn polygons.

## Using the prototype

- **Map:** hover or tap a zone to see its name. Tap it to zoom in and open the
  zone card.
  - **Mouse:** wheel to zoom, drag to pan.
  - **Touch:** pinch to zoom, drag with one finger to pan.
  - **Buttons:** `+` / `−` / **Overview** in the bottom-right corner.
  - **Keyboard** (with the map focused): `+`, `−`, arrow keys, `0` for
    overview, `Esc` to close.
- **Alert Simulator** (right-hand sidebar, collapsible):
  - **Manual tab:** pick a zone, an alert type and a severity, then press
    **TRIGGER ALERT**.
  - **Demo scenarios tab:** runs the three scripted incidents.
  - **CLEAR ALERTS** removes every alert and cancels any scenario that is still
    running.
- **Active alerts** list: sorted CRITICAL → HIGH → MEDIUM → LOW. Tap an entry
  to fly to its zone.
- **Info card:**
  - **ACKNOWLEDGE** marks the alert as acknowledged and stops the sound. The
    zone stays marked, with a dashed outline and no pulse.
  - **VIEW DETAILS** opens the full (simulated) record.
- **Sound** starts only after your first tap or click, because browsers block
  audio until you interact with the page. **Mute** is in the top bar.
- **Edit zones** (bottom-left) is a developer tool. It:
  - outlines every configured zone;
  - shows the cursor position in map coordinates;
  - turns points you tap into a ready-to-paste polygon snippet.

## Project structure

```
public/
  factory-map.jpg            site plan (map background)
src/
  main.tsx                   React entry point
  App.tsx                    layout + wiring (focus, zoom, sound, drawer)
  styles.css                 all styling (design tokens at the top)
  types/
    alerts.ts                Severity, AlertType, FactoryAlert, TriggerAlertInput
    zones.ts                 FactoryZone, Point
  config/
    factoryZones.ts          ★ zone polygons, centroids, equipment
    alertTypes.ts            ★ alert types + simulated descriptions/sensor data
    severity.ts              severity labels, ranks, colours
    demoScenarios.ts         the three demo scenarios
  services/
    alertStore.ts            ★ triggerAlert() — the single alert entry point
    alertFactory.ts          fills in generated details (ID, equipment, sensors)
    alertStream.ts           ★ WebSocket client for a future ML backend
    scenarioRunner.ts        plays demo scenarios through triggerAlert()
  hooks/
    useAlerts.ts             React view of the alert store
    useAlertSound.ts         Web Audio severity patterns
    useMapCamera.ts          zoom / pan camera maths
    useMapGestures.ts        pointer, pinch and wheel gestures
    useElementSize.ts, useClock.ts
  components/
    TopBar, FactoryMap, ZoneLayer, ZoneMarkers, AlertInfoCard, GridRulers,
    MapControls, MapLegend, ZoneEditor, AlertSimulator, ActiveAlerts,
    AlertDetails, SeverityBadge, icons
  utils/
    geometry, zoneGeometry, grid, alerts (sorting/summaries), format
```

## Architecture: one entry point for alerts

```
  Alert Simulator (manual) ─┐
  Demo scenarios ───────────┼──►  triggerAlert({ zoneId, type, severity })  ──►  alert store
  ML / WebSocket feed ──────┘         src/services/alertStore.ts                    │
                                                                                     ▼
                      map highlight · zoom · info card · alert list · sound  ◄── React UI
```

- UI components never create alerts themselves. The simulator, the scenarios
  and the WebSocket client all call `triggerAlert(...)`.
- The store lives outside React.
  - Components read it through `useAlerts()`.
  - `App.tsx` subscribes with `onAlertTriggered(...)` to zoom and play sound.
- In the browser console you can raise alerts by hand:

```js
window.factoryAlerts.triggerAlert({ zoneId: 'a102-casting', type: 'temperature_anomaly', severity: 'medium' })
```

## Connecting a future ML / anomaly-detection backend

1. Create `.env.local` in the project root:
   ```
   VITE_ALERT_WS_URL=ws://localhost:8080/alerts
   ```
2. Restart `npm run dev`. The app connects and reconnects automatically with
   backoff. The top bar shows the source as *ML feed · live*.
3. Send one JSON message per alert, or an array of them:
   ```json
   {
     "zoneId": "a103-body-in-white",
     "type": "equipment_failure",
     "severity": "high",
     "equipmentId": "Robot Cell BIW-R17",
     "description": "Torque threshold exceeded",
     "confidence": 0.93,
     "timestamp": "2026-09-25T14:32:06Z",
     "sensorData": [{ "label": "Motor current", "value": "42 A", "expected": "18–30 A", "outOfRange": true }],
     "suggestedAction": "Inspect motor drive"
   }
   ```
   - Only `zoneId`, `type` and `severity` are required.
   - Messages wrapped as `{ "event": "alert", "data": { ... } }` are accepted
     too.
   - Invalid messages are ignored with a console warning.

Validation and mapping live in `src/services/alertStream.ts`
(`parseAlertEvent`). Replace that file if your backend uses a different
transport, such as SSE or MQTT over WebSocket. Keep the final
`triggerAlert(...)` call.

## Configuration recipes

**Add a zone:** append an entry to `FACTORY_ZONES` in
`src/config/factoryZones.ts`. For example, the *DF* building next to *LN*:

```ts
{
  id: 'df-distribution',       // stable id used by alerts / backend
  code: 'DF',
  name: 'Vehicle Distribution',
  hall: 'Logistics',           // groups the zone dropdown
  polygon: '754,563 800,563 800,611 754,611',
  centroid: { x: 777, y: 587 },
  assets: ['Car Carrier Dock DF-D01', 'Vehicle Tracking Gate DF-G1'],
},
```

Use **Edit zones** to read coordinates off the map. Tap the corners and copy the
generated `polygon` / `centroid` lines.

**Add an alert type:**

1. Add its id to the `AlertType` union in `src/types/alerts.ts`.
2. Add an entry to `ALERT_TYPES` in `src/config/alertTypes.ts`: label,
   category, description templates, sensor templates and suggested actions.
3. Optionally, add it to `ALERT_TYPE_ORDER` to control where it appears in the
   dropdown.

TypeScript reports an error if step 2 is missing.

**Change colours or sounds:**

- Severity colours are CSS variables (`--sev-low` … `--sev-critical`) at the
  top of `src/styles.css`.
- Sound patterns and the master volume are in `src/hooks/useAlertSound.ts`.

## Notes

- All alert data is fictional and generated locally. The UI labels it as
  **Simulation mode / Simulated data**.
- 26 zones are traced from the supplied site plan, in five groups: North Hall
  (8), South Hall (7), Powertrain (4), Logistics (2) and Utilities (5). Unlabelled
  or expansion-only areas (ED, NJ, ZE, LF-2, LF-3, LF/RO, DF, BMA, Battery
  Units, WO KMU) are not zones yet; add them with the recipe above.
- Building codes (A1xx north, A0xx south) come from the Giga Berlin permit
  plan, because the redrawn map shows names only. Utility codes are the labels
  printed on the map.
- Tested in current Chromium. The UI is designed for 1366 × 768 landscape and
  adapts down to a stacked portrait layout below 900 px wide.
