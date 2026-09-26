import {
  useCallback,
  useImperativeHandle,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { FACTORY_ZONES, MAP_HEIGHT, MAP_IMAGE_URL, MAP_WIDTH, getZone } from '../config/factoryZones';
import { useElementSize } from '../hooks/useElementSize';
import { CARD_GAP, CARD_WIDTH, CONTROLS_SPACE, RULER_SIZE, screenToMap, useMapCamera } from '../hooks/useMapCamera';
import { useMapGestures } from '../hooks/useMapGestures';
import type { FactoryAlert } from '../types/alerts';
import type { Point } from '../types/zones';
import { alertsForZone, summarizeByZone } from '../utils/alerts';
import { clamp, round } from '../utils/geometry';
import { gridCell } from '../utils/grid';
import { getZoneGeometry } from '../utils/zoneGeometry';
import { AlertInfoCard, type CardSide } from './AlertInfoCard';
import { GridRulers } from './GridRulers';
import { MapControls } from './MapControls';
import { MapLegend } from './MapLegend';
import { ZoneEditor } from './ZoneEditor';
import { ZoneLayer } from './ZoneLayer';
import { ZoneMarkers } from './ZoneMarkers';

/** Imperative camera API used by App (alerts, list clicks, overview button). */
export interface FactoryMapHandle {
  focusZone: (zoneId: string) => void;
  resetView: () => void;
}

/** What the map is focused on: a zone and optionally one of its alerts. */
export interface MapFocus {
  zoneId: string;
  alertId: string | null;
}

interface FactoryMapProps {
  apiRef: MutableRefObject<FactoryMapHandle | null>;
  alerts: readonly FactoryAlert[];
  focus: MapFocus | null;
  editMode: boolean;
  simulateLabel: string;
  onToggleEdit: () => void;
  onZoneTap: (zoneId: string) => void;
  onBackgroundTap: () => void;
  onSelectAlert: (alertId: string) => void;
  onAcknowledge: (alertId: string) => void;
  onViewDetails: (alertId: string) => void;
  onCloseCard: () => void;
  onSimulateHere: (zoneId: string) => void;
  onOverview: () => void;
}

const EDGE = 12;

interface CardPlacement {
  x: number;
  y: number;
  width: number;
  side: CardSide;
}

/** Places the card beside the zone: right, else left, else below/above, else overlay. */
function placeCard(
  zone: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
  cardHeight: number,
): CardPlacement {
  const width = Math.min(CARD_WIDTH, viewport.width - EDGE * 2 - RULER_SIZE);
  const minX = RULER_SIZE + EDGE;
  // Keep clear of the zoom controls when the viewport is wide enough.
  const maxX = Math.max(minX, viewport.width - width - (viewport.width - width - CONTROLS_SPACE >= minX ? CONTROLS_SPACE : EDGE));
  const minY = RULER_SIZE + EDGE;
  const maxY = Math.max(minY, viewport.height - cardHeight - EDGE);
  const centerY = zone.y + zone.height / 2;
  const sideY = clamp(centerY - cardHeight / 2, minY, maxY);

  const rightX = zone.x + zone.width + CARD_GAP;
  if (rightX <= maxX) return { x: rightX, y: sideY, width, side: 'right' };

  const leftX = zone.x - CARD_GAP - width;
  if (leftX >= minX) return { x: leftX, y: sideY, width, side: 'left' };

  const centeredX = clamp(zone.x + zone.width / 2 - width / 2, minX, maxX);
  const belowY = zone.y + zone.height + CARD_GAP;
  if (belowY + cardHeight <= viewport.height - EDGE) return { x: centeredX, y: belowY, width, side: 'below' };

  const aboveY = zone.y - CARD_GAP - cardHeight;
  if (aboveY >= minY) return { x: centeredX, y: aboveY, width, side: 'above' };

  // No free side: pin to the half of the view facing away from the zone centre.
  const zoneCenterX = zone.x + zone.width / 2;
  return { x: zoneCenterX > viewport.width / 2 ? minX : maxX, y: sideY, width, side: 'overlay' };
}

function isTextInput(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest('input, textarea, select') !== null;
}

export function FactoryMap({
  apiRef,
  alerts,
  focus,
  editMode,
  simulateLabel,
  onToggleEdit,
  onZoneTap,
  onBackgroundTap,
  onSelectAlert,
  onAcknowledge,
  onViewDetails,
  onCloseCard,
  onSimulateHere,
  onOverview,
}: FactoryMapProps) {
  const [viewportRef, viewport] = useElementSize<HTMLDivElement>();
  const camera = useMapCamera(viewport);
  const { view, focusRect, reset, zoomBy, panBy } = camera;

  const [hoveredZoneId, setHoveredZoneId] = useState<string | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [draftPoints, setDraftPoints] = useState<Point[]>([]);
  const [cardHeight, setCardHeight] = useState(300);
  const [imageFailed, setImageFailed] = useState(false);

  useImperativeHandle(
    apiRef,
    () => ({
      focusZone: (zoneId: string) => {
        const zone = getZone(zoneId);
        const geometry = getZoneGeometry(zoneId);
        if (zone && geometry) focusRect(geometry.bbox, zone.centroid, true);
      },
      resetView: reset,
    }),
    [focusRect, reset],
  );

  const { dragging } = useMapGestures(viewportRef, {
    getCamera: camera.getCamera,
    getViewport: camera.getViewport,
    setCamera: camera.setCamera,
    zoomAt: camera.zoomAt,
    onTap: (point, target) => {
      if (editMode) {
        const p = screenToMap(point, view);
        if (p.x >= 0 && p.y >= 0 && p.x <= MAP_WIDTH && p.y <= MAP_HEIGHT) {
          setDraftPoints((prev) => [...prev, { x: round(p.x, 1), y: round(p.y, 1) }]);
        }
        return;
      }
      const zoneElement = target?.closest('[data-zone-id]');
      const zoneId = zoneElement?.getAttribute('data-zone-id');
      if (zoneId) onZoneTap(zoneId);
      else onBackgroundTap();
    },
  });

  const summaries = useMemo(() => summarizeByZone(alerts), [alerts]);
  const focusedZone = focus ? getZone(focus.zoneId) ?? null : null;
  const zoneAlerts = useMemo(() => (focus ? alertsForZone(alerts, focus.zoneId) : []), [alerts, focus]);
  const focusedAlert = focus?.alertId ? zoneAlerts.find((a) => a.id === focus.alertId) ?? null : null;

  const showCard = !!focusedZone && !editMode && viewport.width > 0;
  let placement: CardPlacement | null = null;
  if (showCard && focusedZone) {
    const geometry = getZoneGeometry(focusedZone.id);
    if (geometry) {
      const { bbox } = geometry;
      placement = placeCard(
        {
          x: view.tx + bbox.x * view.scale,
          y: view.ty + bbox.y * view.scale,
          width: bbox.width * view.scale,
          height: bbox.height * view.scale,
        },
        viewport,
        cardHeight,
      );
    }
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape' && (isTextInput(event.target) || event.target !== event.currentTarget)) return;
    const pan = 80;
    switch (event.key) {
      case '+':
      case '=':
        zoomBy(1.4);
        break;
      case '-':
      case '_':
        zoomBy(1 / 1.4);
        break;
      case '0':
        onOverview();
        break;
      case 'ArrowLeft':
        panBy(-pan, 0);
        break;
      case 'ArrowRight':
        panBy(pan, 0);
        break;
      case 'ArrowUp':
        panBy(0, -pan);
        break;
      case 'ArrowDown':
        panBy(0, pan);
        break;
      case 'Escape':
        if (editMode) onToggleEdit();
        else if (focus) onCloseCard();
        else onOverview();
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!editMode) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const p = screenToMap({ x: event.clientX - rect.left, y: event.clientY - rect.top }, view);
    setCursor(p.x >= 0 && p.y >= 0 && p.x <= MAP_WIDTH && p.y <= MAP_HEIGHT ? p : null);
  };

  const handleCardHeight = useCallback((height: number) => setCardHeight(height), []);

  const worldStyle = {
    width: MAP_WIDTH,
    height: MAP_HEIGHT,
    transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
    '--inv': 1 / view.scale,
  } as CSSProperties;

  return (
    <div
      ref={viewportRef}
      className="map-viewport"
      data-motion={camera.motion}
      data-dragging={dragging || undefined}
      data-edit={editMode || undefined}
      data-ready={viewport.width > 0 || undefined}
      tabIndex={0}
      aria-label="Factory site map. Use plus and minus to zoom, arrow keys to pan, 0 for overview."
      onKeyDown={handleKeyDown}
      onPointerMove={handlePointerMove}
      onPointerLeave={() => setCursor(null)}
    >
      <div className="map-world" style={worldStyle}>
        <img
          className="map-image"
          src={MAP_IMAGE_URL}
          width={MAP_WIDTH}
          height={MAP_HEIGHT}
          alt="Gigafactory site plan"
          draggable={false}
          onError={() => setImageFailed(true)}
          onLoad={() => setImageFailed(false)}
        />
        <ZoneLayer
          zones={FACTORY_ZONES}
          summaries={summaries}
          focusedZoneId={focus?.zoneId ?? null}
          hoveredZoneId={hoveredZoneId}
          dimActive={!!focus && !editMode && camera.camera.zoom > 1.2}
          editMode={editMode}
          draftPoints={draftPoints}
          inv={1 / view.scale}
          onHover={setHoveredZoneId}
          onActivate={onZoneTap}
        />
      </div>

      <div className="map-overlay">
        <ZoneMarkers
          zones={FACTORY_ZONES}
          summaries={summaries}
          view={view}
          hoveredZoneId={hoveredZoneId}
          cardZoneId={showCard ? focus?.zoneId ?? null : null}
          editMode={editMode}
        />
        {showCard && focusedZone && placement && (
          <AlertInfoCard
            key={focusedZone.id}
            zone={focusedZone}
            alerts={zoneAlerts}
            alert={focusedAlert}
            x={placement.x}
            y={placement.y}
            width={placement.width}
            side={placement.side}
            onHeight={handleCardHeight}
            onSelectAlert={onSelectAlert}
            onAcknowledge={onAcknowledge}
            onViewDetails={onViewDetails}
            onClose={onCloseCard}
            onSimulateHere={() => onSimulateHere(focusedZone.id)}
            simulateLabel={simulateLabel}
          />
        )}
      </div>

      <GridRulers view={view} highlight={focusedZone ? gridCell(focusedZone.centroid) : null} />

      {editMode && (
        <ZoneEditor
          cursor={cursor}
          points={draftPoints}
          onUndo={() => setDraftPoints((prev) => prev.slice(0, -1))}
          onClear={() => setDraftPoints([])}
          onClose={onToggleEdit}
        />
      )}

      <MapLegend editMode={editMode} onToggleEdit={onToggleEdit} />
      <MapControls zoom={camera.camera.zoom} onZoomIn={() => zoomBy(1.5)} onZoomOut={() => zoomBy(1 / 1.5)} onOverview={onOverview} />

      {imageFailed && (
        <div className="map-missing" data-map-ui role="alert">
          <strong>Site-plan image not found</strong>
          <span>
            Place the map at <code>public/factory-map.jpg</code> and reload. Zones remain usable without it.
          </span>
        </div>
      )}
    </div>
  );
}
