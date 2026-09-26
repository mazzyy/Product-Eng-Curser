import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ActiveAlerts } from './components/ActiveAlerts';
import { AlertDetails } from './components/AlertDetails';
import { AlertSimulator } from './components/AlertSimulator';
import { FactoryMap, type FactoryMapHandle, type MapFocus } from './components/FactoryMap';
import { TopBar } from './components/TopBar';
import { ALERT_TYPES } from './config/alertTypes';
import { getZone } from './config/factoryZones';
import { SEVERITY_META } from './config/severity';
import { useAlerts } from './hooks/useAlerts';
import { useAlertSound } from './hooks/useAlertSound';
import { acknowledgeAlert, clearAlerts, onAlertTriggered, triggerAlert } from './services/alertStore';
import { connectAlertStream, type StreamStatus } from './services/alertStream';
import { cancelScenario } from './services/scenarioRunner';
import type { AlertType, Severity } from './types/alerts';
import { alertsForZone, sortAlerts, summarizeByZone } from './utils/alerts';

export default function App() {
  const alerts = useAlerts();
  const sortedAlerts = useMemo(() => sortAlerts(alerts), [alerts]);
  const summaries = useMemo(() => summarizeByZone(alerts), [alerts]);

  const [muted, setMuted] = useState(false);
  const sound = useAlertSound(muted);
  const stopSound = sound.stop;

  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [detailsAlertId, setDetailsAlertId] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('disabled');

  // Simulator selection lives here so a zone tap can preselect the zone and
  // the zone card can "simulate alert here" with the same type / severity.
  const [simulatorOpen, setSimulatorOpen] = useState(true);
  const [simZoneId, setSimZoneId] = useState('a103-body-in-white');
  const [simType, setSimType] = useState<AlertType>('equipment_failure');
  const [simSeverity, setSimSeverity] = useState<Severity>('high');

  const mapApi = useRef<FactoryMapHandle | null>(null);
  const soundRef = useRef(sound);
  useLayoutEffect(() => {
    soundRef.current = sound;
  });

  // ── Every new alert (simulator, scenario or ML feed) ends up here ──────────
  useEffect(
    () =>
      onAlertTriggered((alert) => {
        setFocus({ zoneId: alert.zoneId, alertId: alert.id });
        mapApi.current?.focusZone(alert.zoneId);
        soundRef.current.play(alert.severity);
      }),
    [],
  );

  // ── Future integration: ML / backend alerts over WebSocket ───────────────
  useEffect(() => {
    const url = import.meta.env.VITE_ALERT_WS_URL;
    if (!url) return undefined;
    return connectAlertStream(url, setStreamStatus);
  }, []);

  // Drop focus / details that point to alerts that no longer exist.
  useEffect(() => {
    if (focus?.alertId && !alerts.some((a) => a.id === focus.alertId)) setFocus(null);
    if (detailsAlertId && !alerts.some((a) => a.id === detailsAlertId)) setDetailsAlertId(null);
  }, [alerts, focus, detailsAlertId]);

  const selectAlert = useCallback(
    (alertId: string) => {
      const alert = alerts.find((a) => a.id === alertId);
      if (!alert) return;
      setEditMode(false);
      setFocus({ zoneId: alert.zoneId, alertId: alert.id });
      mapApi.current?.focusZone(alert.zoneId);
    },
    [alerts],
  );

  const handleZoneTap = useCallback(
    (zoneId: string) => {
      const top = alertsForZone(alerts, zoneId)[0];
      setFocus({ zoneId, alertId: top?.id ?? null });
      setSimZoneId(zoneId);
      mapApi.current?.focusZone(zoneId);
    },
    [alerts],
  );

  const handleAcknowledge = useCallback(
    (alertId: string) => {
      acknowledgeAlert(alertId);
      stopSound();
    },
    [stopSound],
  );

  const handleOverview = useCallback(() => {
    setFocus(null);
    mapApi.current?.resetView();
  }, []);

  const handleClear = useCallback(() => {
    cancelScenario();
    clearAlerts();
    stopSound();
    setFocus(null);
    setDetailsAlertId(null);
    mapApi.current?.resetView();
  }, [stopSound]);

  const closeDetails = useCallback(() => setDetailsAlertId(null), []);

  const handleShowOnMap = useCallback(
    (alertId: string) => {
      setDetailsAlertId(null);
      selectAlert(alertId);
    },
    [selectAlert],
  );

  const toggleEdit = useCallback(() => {
    setEditMode((on) => !on);
    setFocus(null);
  }, []);

  const detailsAlert = detailsAlertId ? alerts.find((a) => a.id === detailsAlertId) ?? null : null;
  const focusedZone = focus ? getZone(focus.zoneId) ?? null : null;
  const simulateLabel = `${SEVERITY_META[simSeverity].label} · ${ALERT_TYPES[simType].label}`;

  return (
    <div className="app">
      <TopBar
        alerts={alerts}
        focusedZone={focusedZone}
        muted={muted}
        soundReady={sound.unlocked}
        streamStatus={streamStatus}
        onToggleMute={() => setMuted((m) => !m)}
        onAlertCountClick={() => {
          if (sortedAlerts[0]) selectAlert(sortedAlerts[0].id);
        }}
      />

      <main className="workspace">
        <section className="map-pane" aria-label="Plant map">
          <FactoryMap
            apiRef={mapApi}
            alerts={alerts}
            focus={focus}
            editMode={editMode}
            simulateLabel={simulateLabel}
            onToggleEdit={toggleEdit}
            onZoneTap={handleZoneTap}
            onBackgroundTap={() => setFocus(null)}
            onSelectAlert={selectAlert}
            onAcknowledge={handleAcknowledge}
            onViewDetails={setDetailsAlertId}
            onCloseCard={() => setFocus(null)}
            onSimulateHere={(zoneId: string) => triggerAlert({ zoneId, type: simType, severity: simSeverity })}
            onOverview={handleOverview}
          />
        </section>

        <aside className="sidebar" aria-label="Alerts and simulator">
          <ActiveAlerts
            alerts={sortedAlerts}
            summaries={summaries}
            selectedAlertId={focus?.alertId ?? null}
            onSelect={selectAlert}
          />
          <AlertSimulator
            open={simulatorOpen}
            onToggle={() => setSimulatorOpen((o) => !o)}
            zoneId={simZoneId}
            onZoneChange={setSimZoneId}
            type={simType}
            onTypeChange={setSimType}
            severity={simSeverity}
            onSeverityChange={setSimSeverity}
            alertCount={alerts.length}
            onClear={handleClear}
            onOverview={handleOverview}
          />
        </aside>
      </main>

      <AlertDetails
        alert={detailsAlert}
        onClose={closeDetails}
        onAcknowledge={handleAcknowledge}
        onShowOnMap={handleShowOnMap}
      />
    </div>
  );
}
