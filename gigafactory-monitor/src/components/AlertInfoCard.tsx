import { useEffect } from 'react';
import { ALERT_TYPES } from '../config/alertTypes';
import { useElementSize } from '../hooks/useElementSize';
import type { FactoryAlert } from '../types/alerts';
import type { FactoryZone } from '../types/zones';
import { formatTime } from '../utils/format';
import { gridRef } from '../utils/grid';
import { SeverityBadge } from './SeverityBadge';
import { IconBolt, IconCheck, IconChevron, IconClose } from './icons';

export type CardSide = 'right' | 'left' | 'below' | 'above' | 'overlay';

interface AlertInfoCardProps {
  zone: FactoryZone;
  /** All alerts of this zone, sorted by priority. */
  alerts: FactoryAlert[];
  /** Alert shown in the card; null shows the zone status card. */
  alert: FactoryAlert | null;
  x: number;
  y: number;
  width: number;
  side: CardSide;
  onHeight: (height: number) => void;
  onSelectAlert: (alertId: string) => void;
  onAcknowledge: (alertId: string) => void;
  onViewDetails: (alertId: string) => void;
  onClose: () => void;
  onSimulateHere: () => void;
  /** e.g. "HIGH · Equipment Failure" — current simulator selection. */
  simulateLabel: string;
}

/**
 * Floating card placed next to the focused zone (screen space). Shows the
 * selected alert, or — for a zone without alerts — its status.
 */
export function AlertInfoCard({
  zone,
  alerts,
  alert,
  x,
  y,
  width,
  side,
  onHeight,
  onSelectAlert,
  onAcknowledge,
  onViewDetails,
  onClose,
  onSimulateHere,
  simulateLabel,
}: AlertInfoCardProps) {
  const [cardRef, size] = useElementSize<HTMLElement>();

  useEffect(() => {
    if (size.height > 0) onHeight(size.height);
  }, [size.height, onHeight]);

  const index = alert ? alerts.findIndex((a) => a.id === alert.id) : -1;
  const step = (delta: number) => {
    if (alerts.length < 2 || index < 0) return;
    onSelectAlert(alerts[(index + delta + alerts.length) % alerts.length].id);
  };

  return (
    <div className="info-card-anchor" data-map-ui style={{ transform: `translate(${x}px, ${y}px)` }}>
      <section
        ref={cardRef}
        className="info-card"
        data-side={side}
        data-severity={alert?.severity}
        data-ack={alert?.acknowledged || undefined}
        style={{ width }}
        aria-label={`${zone.name} ${zone.code}`}
        aria-live="polite"
      >
        <header className="info-card__head">
          <div className="info-card__title">
            <div className="info-card__meta">
              <span className="code-chip">{zone.code}</span>
              <span className="info-card__hall">{zone.hall}</span>
              <span className="info-card__grid" title="Site-plan grid reference">
                Grid {gridRef(zone.centroid)}
              </span>
            </div>
            <h2>{zone.name}</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close card">
            <IconClose size={18} />
          </button>
        </header>

        {alert ? (
          <>
            {alerts.length > 1 && (
              <div className="info-card__pager">
                <button type="button" className="icon-btn icon-btn--small" onClick={() => step(-1)} aria-label="Previous alert in zone">
                  <IconChevron direction="left" size={16} />
                </button>
                <span>
                  Alert {index + 1} of {alerts.length} in zone
                </span>
                <button type="button" className="icon-btn icon-btn--small" onClick={() => step(1)} aria-label="Next alert in zone">
                  <IconChevron direction="right" size={16} />
                </button>
              </div>
            )}

            <div className="info-card__body">
              <div className="info-card__severity">
                <SeverityBadge severity={alert.severity} size="lg" muted={alert.acknowledged} />
                <span className="info-card__type">{ALERT_TYPES[alert.type].label}</span>
              </div>
              <p className="info-card__equipment">{alert.equipmentId}</p>
              <p className="info-card__desc">{alert.description}</p>

              <dl className="facts">
                <div>
                  <dt>Detected</dt>
                  <dd className="num">{formatTime(alert.timestamp)}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>
                    <span className="status-pill" data-ack={alert.acknowledged || undefined}>
                      {alert.acknowledged ? 'ACKNOWLEDGED' : 'ACTIVE'}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Alert ID</dt>
                  <dd className="num">{alert.id}</dd>
                </div>
              </dl>
            </div>

            <div className="info-card__actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => onAcknowledge(alert.id)}
                disabled={alert.acknowledged}
              >
                {alert.acknowledged ? (
                  <>
                    <IconCheck size={18} />
                    Acknowledged
                  </>
                ) : (
                  'Acknowledge'
                )}
              </button>
              <button type="button" className="btn" onClick={() => onViewDetails(alert.id)}>
                View details
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="info-card__body">
              <p className="zone-status">
                <span className="dot dot--ok" aria-hidden="true" />
                Normal operation — no active alerts
              </p>
              <h3 className="info-card__subhead">Monitored equipment</h3>
              <ul className="asset-list">
                {zone.assets.slice(0, 5).map((asset) => (
                  <li key={asset}>{asset}</li>
                ))}
              </ul>
            </div>
            <div className="info-card__actions info-card__actions--single">
              <button type="button" className="btn" onClick={onSimulateHere}>
                <IconBolt size={18} />
                <span className="btn__stack">
                  <span>Simulate alert here</span>
                  <small>{simulateLabel}</small>
                </span>
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
