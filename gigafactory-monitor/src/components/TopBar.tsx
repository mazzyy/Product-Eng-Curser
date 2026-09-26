import { SEVERITY_META } from '../config/severity';
import { useClock } from '../hooks/useClock';
import type { StreamStatus } from '../services/alertStream';
import type { FactoryAlert } from '../types/alerts';
import type { FactoryZone } from '../types/zones';
import { highestSeverity } from '../utils/alerts';
import { formatDate, formatTime } from '../utils/format';
import { IconCheck, IconChevron, IconPlantMark, IconSpeaker, IconSpeakerOff, SeverityIcon } from './icons';

interface TopBarProps {
  alerts: readonly FactoryAlert[];
  focusedZone: FactoryZone | null;
  muted: boolean;
  soundReady: boolean;
  streamStatus: StreamStatus;
  onToggleMute: () => void;
  onAlertCountClick: () => void;
}

const STREAM_LABEL: Record<StreamStatus, string> = {
  disabled: 'Simulator',
  connecting: 'ML feed · connecting',
  connected: 'ML feed · live',
  reconnecting: 'ML feed · reconnecting',
};

export function TopBar({
  alerts,
  focusedZone,
  muted,
  soundReady,
  streamStatus,
  onToggleMute,
  onAlertCountClick,
}: TopBarProps) {
  const now = useClock(1000);
  const open = alerts.filter((a) => !a.acknowledged);
  const worst = highestSeverity(open.length > 0 ? open : alerts);

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <IconPlantMark size={26} />
        <div className="topbar__titles">
          <span className="topbar__title">Factory Monitoring</span>
          <nav className="topbar__crumb" aria-label="Location">
            <span>Plant Overview</span>
            {focusedZone && (
              <>
                <IconChevron direction="right" size={14} />
                <span className="topbar__crumb-zone">
                  {focusedZone.code} {focusedZone.shortName ?? focusedZone.name}
                </span>
              </>
            )}
          </nav>
        </div>
      </div>

      <div className="topbar__status">
        <span className="sim-chip" title="All alerts are generated locally by the simulator">
          Simulation mode
        </span>

        <span className="status-item" title="Frontend prototype running">
          <span className="dot dot--ok" aria-hidden="true" />
          System online
        </span>

        <span className="status-item status-item--feed" data-state={streamStatus} title="Alert source">
          <span className="status-item__label">Source</span>
          {STREAM_LABEL[streamStatus]}
        </span>

        <button
          type="button"
          className="alert-count"
          data-severity={worst ?? undefined}
          data-clear={alerts.length === 0 || undefined}
          onClick={onAlertCountClick}
          disabled={alerts.length === 0}
          aria-label={`${alerts.length} active alerts, ${open.length} unacknowledged`}
        >
          {worst ? <SeverityIcon severity={worst} size={18} /> : <IconCheck size={18} />}
          <span className="alert-count__num num">{alerts.length}</span>
          <span className="alert-count__label">
            Active alerts
            {open.length > 0 && worst && (
              <small>
                {open.length} unack · {SEVERITY_META[worst].label}
              </small>
            )}
          </span>
        </button>

        <div className="clock" aria-label="Current time">
          <span className="clock__time num">{formatTime(now)}</span>
          <span className="clock__date">{formatDate(now)}</span>
        </div>

        <button
          type="button"
          className="mute-btn"
          aria-pressed={muted}
          onClick={onToggleMute}
          data-pending={(!muted && !soundReady) || undefined}
          title={!soundReady ? 'Audio starts after your first tap or click' : muted ? 'Sound is muted' : 'Sound is on'}
        >
          {muted ? <IconSpeakerOff size={20} /> : <IconSpeaker size={20} />}
          <span>{muted ? 'Unmute' : 'Mute'}</span>
        </button>
      </div>
    </header>
  );
}
