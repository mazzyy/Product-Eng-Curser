import type { MapView } from '../hooks/useMapCamera';
import { CELL_HEIGHT, CELL_WIDTH, GRID_COLS, GRID_ROWS, columnLabel } from '../utils/grid';

interface GridRulersProps {
  view: MapView;
  /** Grid cell of the focused zone, highlighted on both rulers. */
  highlight: { col: number; row: number } | null;
}

/**
 * Site-plan grid rulers (A–L / 1–9) along the top and left edge of the map.
 * They track the camera so a zone can always be referenced as e.g. "C2".
 */
export function GridRulers({ view, highlight }: GridRulersProps) {
  const cellW = CELL_WIDTH * view.scale;
  const cellH = CELL_HEIGHT * view.scale;

  return (
    <>
      <div className="ruler ruler--x" aria-hidden="true">
        {highlight && (
          <div
            className="ruler__band"
            style={{ transform: `translateX(${view.tx + highlight.col * cellW}px)`, width: cellW }}
          />
        )}
        {Array.from({ length: GRID_COLS + 1 }, (_, i) => (
          <div key={`t${i}`} className="ruler__tick" style={{ transform: `translateX(${view.tx + i * cellW}px)` }} />
        ))}
        {Array.from({ length: GRID_COLS }, (_, i) => (
          <div
            key={`l${i}`}
            className="ruler__label"
            data-active={highlight?.col === i || undefined}
            style={{ transform: `translate(${view.tx + (i + 0.5) * cellW}px, 0px) translateX(-50%)` }}
          >
            {columnLabel(i)}
          </div>
        ))}
      </div>
      <div className="ruler ruler--y" aria-hidden="true">
        {highlight && (
          <div
            className="ruler__band"
            style={{ transform: `translateY(${view.ty + highlight.row * cellH}px)`, height: cellH }}
          />
        )}
        {Array.from({ length: GRID_ROWS + 1 }, (_, i) => (
          <div key={`t${i}`} className="ruler__tick" style={{ transform: `translateY(${view.ty + i * cellH}px)` }} />
        ))}
        {Array.from({ length: GRID_ROWS }, (_, i) => (
          <div
            key={`l${i}`}
            className="ruler__label"
            data-active={highlight?.row === i || undefined}
            style={{ transform: `translate(0px, ${view.ty + (i + 0.5) * cellH}px) translateY(-50%)` }}
          >
            {i + 1}
          </div>
        ))}
      </div>
      <div className="ruler-corner" aria-hidden="true" />
    </>
  );
}
