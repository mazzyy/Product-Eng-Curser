import { MAX_ZOOM, MIN_ZOOM } from '../hooks/useMapCamera';
import { IconMinus, IconOverview, IconPlus } from './icons';

interface MapControlsProps {
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onOverview: () => void;
}

export function MapControls({ zoom, onZoomIn, onZoomOut, onOverview }: MapControlsProps) {
  return (
    <div className="map-controls" data-map-ui>
      <div className="map-controls__zoom" role="group" aria-label="Zoom">
        <button type="button" className="map-btn" onClick={onZoomIn} disabled={zoom >= MAX_ZOOM - 0.01} aria-label="Zoom in">
          <IconPlus />
        </button>
        <div className="map-controls__readout" title="Zoom level (1.0× = whole plant)">
          {zoom.toFixed(1)}×
        </div>
        <button type="button" className="map-btn" onClick={onZoomOut} disabled={zoom <= MIN_ZOOM + 0.01} aria-label="Zoom out">
          <IconMinus />
        </button>
      </div>
      <button type="button" className="map-btn map-btn--labeled" onClick={onOverview} aria-label="Return to plant overview">
        <IconOverview />
        <span>Overview</span>
      </button>
    </div>
  );
}
