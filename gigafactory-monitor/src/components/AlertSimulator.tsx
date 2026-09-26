import { useState, useSyncExternalStore, type ChangeEvent } from 'react';
import { ALERT_TYPE_LIST } from '../config/alertTypes';
import { DEMO_SCENARIOS } from '../config/demoScenarios';
import { zonesByHall } from '../config/factoryZones';
import { SEVERITIES, SEVERITY_META } from '../config/severity';
import { triggerAlert } from '../services/alertStore';
import { getRunningScenarioIds, runScenario, subscribeToScenario } from '../services/scenarioRunner';
import type { AlertType, Severity } from '../types/alerts';
import { IconBolt, IconChevron, IconOverview, IconPlay, IconTrash, SeverityIcon } from './icons';

interface AlertSimulatorProps {
  open: boolean;
  onToggle: () => void;
  zoneId: string;
  onZoneChange: (zoneId: string) => void;
  type: AlertType;
  onTypeChange: (type: AlertType) => void;
  severity: Severity;
  onSeverityChange: (severity: Severity) => void;
  alertCount: number;
  onClear: () => void;
  onOverview: () => void;
}

type Tab = 'manual' | 'scenarios';

const ZONE_GROUPS = zonesByHall();

/**
 * Developer / demo tool. It raises alerts ONLY through triggerAlert(...),
 * the same entry point a future ML backend will use.
 */
export function AlertSimulator({
  open,
  onToggle,
  zoneId,
  onZoneChange,
  type,
  onTypeChange,
  severity,
  onSeverityChange,
  alertCount,
  onClear,
  onOverview,
}: AlertSimulatorProps) {
  const [tab, setTab] = useState<Tab>('manual');
  const runningScenarios = useSyncExternalStore(subscribeToScenario, getRunningScenarioIds, getRunningScenarioIds);

  const handleTrigger = () => {
    // ── The manual trigger. Same call a backend event ends up in. ──
    triggerAlert({ zoneId, type, severity });
  };

  return (
    <section className="panel simulator" data-open={open || undefined} aria-labelledby="simulator-title">
      <button type="button" className="simulator__toggle" onClick={onToggle} aria-expanded={open} aria-controls="simulator-body">
        <span id="simulator-title" className="simulator__title">
          Alert Simulator
        </span>
        <span className="dev-tag">Dev tool</span>
        <IconChevron direction={open ? 'down' : 'up'} size={18} />
      </button>

      {open && (
        <div id="simulator-body" className="simulator__body">
          <div className="tabs" role="tablist" aria-label="Simulator mode">
            <button type="button" role="tab" className="tab" aria-selected={tab === 'manual'} onClick={() => setTab('manual')}>
              Manual
            </button>
            <button
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === 'scenarios'}
              onClick={() => setTab('scenarios')}
            >
              Demo scenarios
            </button>
          </div>

          {tab === 'manual' ? (
            <div className="simulator__form" role="tabpanel">
              <label className="field">
                <span className="field__label">Zone</span>
                <select
                  className="select"
                  value={zoneId}
                  onChange={(event: ChangeEvent<HTMLSelectElement>) => onZoneChange(event.target.value)}
                >
                  {ZONE_GROUPS.map((group) => (
                    <optgroup key={group.hall} label={group.hall}>
                      {group.zones.map((zone) => (
                        <option key={zone.id} value={zone.id}>
                          {zone.code} · {zone.shortName ?? zone.name}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>

              <label className="field">
                <span className="field__label">Alert type</span>
                <select
                  className="select"
                  value={type}
                  onChange={(event: ChangeEvent<HTMLSelectElement>) => onTypeChange(event.target.value as AlertType)}
                >
                  {ALERT_TYPE_LIST.map((definition) => (
                    <option key={definition.id} value={definition.id}>
                      {definition.label}
                    </option>
                  ))}
                </select>
              </label>

              <fieldset className="field">
                <legend className="field__label">Severity</legend>
                <div className="sev-seg">
                  {SEVERITIES.map((level) => (
                    <button
                      key={level}
                      type="button"
                      className="sev-seg__btn"
                      data-severity={level}
                      aria-pressed={severity === level}
                      onClick={() => onSeverityChange(level)}
                      title={SEVERITY_META[level].soundLabel}
                    >
                      <SeverityIcon severity={level} size={16} />
                      <span>{SEVERITY_META[level].label}</span>
                    </button>
                  ))}
                </div>
              </fieldset>

              <button type="button" className="btn btn--trigger" data-severity={severity} onClick={handleTrigger}>
                <IconBolt size={20} />
                Trigger alert
              </button>
            </div>
          ) : (
            <ul className="scenario-list" role="tabpanel">
              {DEMO_SCENARIOS.map((scenario) => {
                const running = runningScenarios.includes(scenario.id);
                return (
                  <li key={scenario.id}>
                    <button
                      type="button"
                      className="scenario"
                      data-running={running || undefined}
                      onClick={() => runScenario(scenario)}
                    >
                      <span className="scenario__icons" aria-hidden="true">
                        {scenario.steps.map((step, i) => (
                          <span key={i} className="scenario__sev" data-severity={step.alert.severity}>
                            <SeverityIcon severity={step.alert.severity} size={16} />
                          </span>
                        ))}
                      </span>
                      <span className="scenario__text">
                        <span className="scenario__title">{scenario.title}</span>
                        <span className="scenario__summary">{scenario.summary}</span>
                      </span>
                      <span className="scenario__run" aria-label={running ? 'Running' : 'Run scenario'}>
                        {running ? <span className="scenario__spinner" aria-hidden="true" /> : <IconPlay size={16} />}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="simulator__footer">
            <button
              type="button"
              className="btn btn--danger"
              onClick={onClear}
              disabled={alertCount === 0 && runningScenarios.length === 0}
            >
              <IconTrash size={18} />
              Clear alerts
            </button>
            <button type="button" className="btn" onClick={onOverview}>
              <IconOverview size={18} />
              Return to overview
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
