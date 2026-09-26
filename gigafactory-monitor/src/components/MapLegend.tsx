import { SEVERITIES, SEVERITY_META } from '../config/severity';
import { IconEdit, SeverityIcon } from './icons';

interface MapLegendProps {
  editMode: boolean;
  onToggleEdit: () => void;
}

export function MapLegend({ editMode, onToggleEdit }: MapLegendProps) {
  return (
    <div className="map-legend" data-map-ui>
      <ul className="map-legend__list" aria-label="Severity legend">
        {[...SEVERITIES].reverse().map((severity) => (
          <li key={severity} className="map-legend__item" data-severity={severity} title={SEVERITY_META[severity].soundLabel}>
            <SeverityIcon severity={severity} size={15} />
            <span>{SEVERITY_META[severity].title}</span>
          </li>
        ))}
        <li className="map-legend__item map-legend__item--ack">
          <span className="map-legend__dash" aria-hidden="true" />
          <span>Acknowledged</span>
        </li>
      </ul>
      <button
        type="button"
        className="map-legend__edit"
        aria-pressed={editMode}
        onClick={onToggleEdit}
        title="Developer tool: show zone boundaries and build polygon coordinates"
      >
        <IconEdit size={16} />
        <span>{editMode ? 'Exit zone edit' : 'Edit zones'}</span>
      </button>
    </div>
  );
}
