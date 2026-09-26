import { ALERT_TYPES } from '../config/alertTypes';
import { getZone } from '../config/factoryZones';
import { useClock } from '../hooks/useClock';
import type { FactoryAlert } from '../types/alerts';
import type { ZoneAlertSummary } from '../utils/alerts';
import { formatAge, formatTime } from '../utils/format';
import { SeverityBadge } from './SeverityBadge';
import { IconCheck } from './icons';

interface ActiveAlertsProps {
  /** Already sorted: CRITICAL → HIGH → MEDIUM → LOW. */
  alerts: FactoryAlert[];
  summaries: Record<string, ZoneAlertSummary>;
  selectedAlertId: string | null;
  onSelect: (alertId: string) => void;
}

export function ActiveAlerts({ alerts, summaries, selectedAlertId, onSelect }: ActiveAlertsProps) {
  const now = useClock(10_000);
  const unacknowledged = alerts.filter((a) => !a.acknowledged).length;

  return (
    <section className="panel alerts-panel" aria-labelledby="active-alerts-title">
      <header className="panel__head">
        <h2 id="active-alerts-title">Active alerts</h2>
        <span className="count-pill" data-zero={alerts.length === 0 || undefined}>
          {alerts.length}
        </span>
        {alerts.length > 0 && <span className="panel__sub">{unacknowledged} unacknowledged</span>}
      </header>

      {alerts.length === 0 ? (
        <div className="alerts-empty">
          <span className="alerts-empty__icon" aria-hidden="true">
            <IconCheck size={20} />
          </span>
          <p>
            <strong>All zones normal</strong>
            <span>Trigger an alert or run a demo scenario from the simulator below.</span>
          </p>
        </div>
      ) : (
        <ul className="alert-list" aria-label="Alerts sorted by severity">
          {alerts.map((alert) => {
            const zone = getZone(alert.zoneId);
            const zoneCount = summaries[alert.zoneId]?.count ?? 1;
            const zoneName = zone ? `${zone.code} ${zone.shortName ?? zone.name}` : alert.zoneId;
            return (
              <li key={alert.id}>
                <button
                  type="button"
                  className="alert-item"
                  data-severity={alert.severity}
                  data-ack={alert.acknowledged || undefined}
                  data-selected={alert.id === selectedAlertId || undefined}
                  aria-current={alert.id === selectedAlertId ? 'true' : undefined}
                  onClick={() => onSelect(alert.id)}
                >
                  <span className="alert-item__row">
                    <SeverityBadge severity={alert.severity} size="sm" muted={alert.acknowledged} />
                    <span
                      className="alert-item__zone"
                      title={zoneCount > 1 ? `${zoneName} — ${zoneCount} alerts in this zone` : zoneName}
                    >
                      {zoneName}
                      {zoneCount > 1 && <span className="alert-item__zonecount">×{zoneCount}</span>}
                    </span>
                    <span className="alert-item__time num" title={formatAge(alert.timestamp, now)}>
                      {formatTime(alert.timestamp)}
                    </span>
                  </span>
                  <span className="alert-item__row alert-item__row--sub">
                    <span className="alert-item__type">
                      {ALERT_TYPES[alert.type].label}
                      <span className="alert-item__equip"> · {alert.equipmentId}</span>
                    </span>
                    <span className="alert-item__state" data-ack={alert.acknowledged || undefined}>
                      {alert.acknowledged ? (
                        <>
                          <IconCheck size={13} /> ACK
                        </>
                      ) : (
                        'UNACK'
                      )}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
