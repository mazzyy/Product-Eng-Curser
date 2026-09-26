import type { FactoryZone } from '../types/zones';
import { mapToScreen, type MapView } from '../hooks/useMapCamera';
import type { ZoneAlertSummary } from '../utils/alerts';
import { SeverityIcon } from './icons';

interface ZoneMarkersProps {
  zones: FactoryZone[];
  summaries: Record<string, ZoneAlertSummary>;
  view: MapView;
  hoveredZoneId: string | null;
  cardZoneId: string | null;
  editMode: boolean;
}

/**
 * Screen-space layer (not scaled) for markers and labels so text stays crisp
 * and readable at every zoom level. Each zone gets a permanent anchor at its
 * centroid; the anchor uses the same transition as the map, so markers move
 * in lock-step with the zoom animation.
 */
export function ZoneMarkers({ zones, summaries, view, hoveredZoneId, cardZoneId, editMode }: ZoneMarkersProps) {
  return (
    <div className="zone-markers" aria-hidden="true">
      {zones.map((zone) => {
        const point = mapToScreen(zone.centroid, view);
        const summary = summaries[zone.id];
        const beacon =
          !!summary && !summary.allAcknowledged && (summary.severity === 'high' || summary.severity === 'critical');
        const showTag = editMode || (hoveredZoneId === zone.id && cardZoneId !== zone.id);
        return (
          <div
            key={zone.id}
            className="zone-anchor"
            data-size={beacon ? 'lg' : 'sm'}
            data-has-marker={summary ? true : undefined}
            style={{ transform: `translate(${point.x}px, ${point.y}px)` }}
          >
            {summary && (
              <>
                <div
                  className="marker"
                  data-severity={summary.severity}
                  data-beacon={beacon || undefined}
                  data-ack={summary.allAcknowledged || undefined}
                >
                  <SeverityIcon severity={summary.severity} size={beacon ? 18 : 14} />
                </div>
                <div className="marker-label" data-severity={summary.severity} data-ack={summary.allAcknowledged || undefined}>
                  <span>{zone.code}</span>
                  {summary.count > 1 && <span className="marker-label__count">{summary.count} alerts</span>}
                </div>
              </>
            )}
            {showTag && (
              <div className="zone-tag" data-edit={editMode || undefined}>
                {editMode ? (
                  zone.id
                ) : (
                  <>
                    <strong>{zone.code}</strong> {zone.name}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
