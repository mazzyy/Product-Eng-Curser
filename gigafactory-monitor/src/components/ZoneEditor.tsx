import { useRef, useState } from 'react';
import type { Point } from '../types/zones';
import { formatPolygon, polygonCentroid, round } from '../utils/geometry';
import { IconClose, IconCopy, IconUndo, IconTrash } from './icons';

interface ZoneEditorProps {
  cursor: Point | null;
  points: Point[];
  onUndo: () => void;
  onClear: () => void;
  onClose: () => void;
}

/**
 * Developer helper for configuring zones: shows the cursor position in map
 * units and turns tapped points into a ready-to-paste FactoryZone snippet.
 */
export function ZoneEditor({ cursor, points, onUndo, onClear, onClose }: ZoneEditorProps) {
  const [copied, setCopied] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  const centroid = polygonCentroid(points);
  const snippet =
    points.length >= 3
      ? `polygon: '${formatPolygon(points)}',\ncentroid: { x: ${round(centroid.x)}, y: ${round(centroid.y)} },`
      : `// Tap ${3 - points.length} more point${points.length === 2 ? '' : 's'} on the map`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
    } catch {
      textRef.current?.select();
      document.execCommand('copy');
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section className="zone-editor" data-map-ui aria-label="Zone editor">
      <header className="zone-editor__head">
        <div>
          <span className="eyebrow">Developer tool</span>
          <h2>Zone editor</h2>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close zone editor">
          <IconClose size={18} />
        </button>
      </header>
      <p className="zone-editor__hint">
        Boundaries of all configured zones are outlined. Tap the map to place polygon points, then paste the
        snippet into <code>src/config/factoryZones.ts</code>.
      </p>
      <dl className="zone-editor__readout">
        <div>
          <dt>Cursor</dt>
          <dd>{cursor ? `${round(cursor.x)}, ${round(cursor.y)}` : '—'}</dd>
        </div>
        <div>
          <dt>Points</dt>
          <dd>{points.length}</dd>
        </div>
      </dl>
      <textarea ref={textRef} className="zone-editor__snippet" readOnly value={snippet} rows={3} spellCheck={false} />
      <div className="zone-editor__actions">
        <button type="button" className="btn btn--small" onClick={copy} disabled={points.length < 3}>
          <IconCopy size={16} />
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button type="button" className="btn btn--small" onClick={onUndo} disabled={points.length === 0}>
          <IconUndo size={16} />
          Undo
        </button>
        <button type="button" className="btn btn--small" onClick={onClear} disabled={points.length === 0}>
          <IconTrash size={16} />
          Clear
        </button>
      </div>
    </section>
  );
}
