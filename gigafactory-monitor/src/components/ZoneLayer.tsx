import { useMemo, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { MAP_HEIGHT, MAP_WIDTH } from '../config/factoryZones';
import { SEVERITY_META } from '../config/severity';
import type { FactoryZone, Point } from '../types/zones';
import type { ZoneAlertSummary } from '../utils/alerts';
import { CELL_HEIGHT, CELL_WIDTH, GRID_COLS, GRID_ROWS } from '../utils/grid';
import { ZONE_GEOMETRY } from '../utils/zoneGeometry';

interface ZoneLayerProps {
  zones: FactoryZone[];
  summaries: Record<string, ZoneAlertSummary>;
  focusedZoneId: string | null;
  hoveredZoneId: string | null;
  /** Darken everything except the focused / alerting zones. */
  dimActive: boolean;
  editMode: boolean;
  draftPoints: Point[];
  /** 1 / current scale — keeps edit-mode dots a constant screen size. */
  inv: number;
  onHover: (zoneId: string | null) => void;
  onActivate: (zoneId: string) => void;
}

/**
 * SVG overlay drawn in MAP UNITS (viewBox = original image size), so it
 * scales together with the map image inside the same transformed element.
 */
export function ZoneLayer({
  zones,
  summaries,
  focusedZoneId,
  hoveredZoneId,
  dimActive,
  editMode,
  draftPoints,
  inv,
  onHover,
  onActivate,
}: ZoneLayerProps) {
  // Dim mask = whole map minus the zones that should stay bright.
  const dimPath = useMemo(() => {
    const holes = zones
      .filter((zone) => zone.id === focusedZoneId || summaries[zone.id])
      .map((zone) => ZONE_GEOMETRY[zone.id]?.path ?? '')
      .join('');
    return `M0 0H${MAP_WIDTH}V${MAP_HEIGHT}H0Z${holes}`;
  }, [zones, summaries, focusedZoneId]);

  return (
    <svg
      className="zone-layer"
      viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
      width={MAP_WIDTH}
      height={MAP_HEIGHT}
      role="group"
      aria-label="Factory zones"
    >
      <path className="zone-dim" d={dimPath} fillRule="evenodd" data-active={dimActive || undefined} />

      {editMode && (
        <g className="edit-grid" aria-hidden="true">
          {Array.from({ length: GRID_COLS - 1 }, (_, i) => (
            <line key={`c${i}`} x1={(i + 1) * CELL_WIDTH} y1={0} x2={(i + 1) * CELL_WIDTH} y2={MAP_HEIGHT} />
          ))}
          {Array.from({ length: GRID_ROWS - 1 }, (_, i) => (
            <line key={`r${i}`} x1={0} y1={(i + 1) * CELL_HEIGHT} x2={MAP_WIDTH} y2={(i + 1) * CELL_HEIGHT} />
          ))}
        </g>
      )}

      {zones.map((zone) => {
        const summary = summaries[zone.id];
        const state = summary ? (summary.allAcknowledged ? 'ack' : 'alert') : 'idle';
        const label = summary
          ? `${zone.code} ${zone.name}: ${summary.count} alert${summary.count === 1 ? '' : 's'}, highest ${SEVERITY_META[summary.severity].title}`
          : `${zone.code} ${zone.name}: normal`;
        return (
          <g
            key={zone.id}
            className="zone"
            data-zone-id={zone.id}
            data-state={state}
            data-severity={summary?.severity}
            data-focused={zone.id === focusedZoneId || undefined}
            data-hovered={zone.id === hoveredZoneId || undefined}
            tabIndex={editMode ? -1 : 0}
            role="button"
            aria-label={label}
            onPointerEnter={(event: ReactPointerEvent<SVGGElement>) => {
              if (event.pointerType === 'mouse') onHover(zone.id);
            }}
            onPointerLeave={(event: ReactPointerEvent<SVGGElement>) => {
              if (event.pointerType === 'mouse') onHover(null);
            }}
            onFocus={() => onHover(zone.id)}
            onBlur={() => onHover(null)}
            onKeyDown={(event: ReactKeyboardEvent<SVGGElement>) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.stopPropagation();
                onActivate(zone.id);
              }
            }}
          >
            {summary && <polygon className="zone-glow" points={zone.polygon} />}
            <polygon className="zone-shape" points={zone.polygon} />
            {editMode &&
              ZONE_GEOMETRY[zone.id]?.points.map((p, i) => (
                <circle key={i} className="zone-vertex" cx={p.x} cy={p.y} r={2.6 * inv} />
              ))}
          </g>
        );
      })}

      {editMode && draftPoints.length > 0 && (
        <g className="draft" aria-hidden="true">
          {draftPoints.length >= 3 && (
            <polygon className="draft-fill" points={draftPoints.map((p) => `${p.x},${p.y}`).join(' ')} />
          )}
          <polyline className="draft-line" points={draftPoints.map((p) => `${p.x},${p.y}`).join(' ')} />
          {draftPoints.map((p, i) => (
            <circle key={i} className="draft-point" cx={p.x} cy={p.y} r={3.6 * inv} />
          ))}
        </g>
      )}
    </svg>
  );
}
