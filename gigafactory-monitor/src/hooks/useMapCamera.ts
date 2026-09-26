import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { MAP_HEIGHT, MAP_WIDTH } from '../config/factoryZones';
import type { Point } from '../types/zones';
import { clamp, type Rect } from '../utils/geometry';

/**
 * Map camera: what part of the map is visible and how large.
 *
 * The camera is stored in MAP UNITS (independent of screen size):
 *   cx, cy  map point shown at the centre of the viewport
 *   zoom    1 = whole map fits the viewport ("overview"), 2 = twice as large …
 *
 * The rendered CSS transform is derived from it:
 *   scale = fitScale(viewport) × zoom
 *   translate = viewportCentre − (cx, cy) × scale
 */

export interface Camera {
  cx: number;
  cy: number;
  zoom: number;
}

/** smooth = animated zoom (700 ms), quick = wheel steps, none = drag / resize. */
export type CameraMotion = 'smooth' | 'quick' | 'none';

export interface Viewport {
  width: number;
  height: number;
}

export interface MapView {
  /** Screen px per map unit. */
  scale: number;
  /** Scale at zoom 1. */
  fit: number;
  tx: number;
  ty: number;
}

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 6;
/** Alert focus zoom range ("approximately 2–3 depending on area size"). */
export const FOCUS_MIN_ZOOM = 1.6;
export const FOCUS_MAX_ZOOM = 3;
/** Screen padding around the map in overview. */
export const FIT_PADDING = 28;
/** Info-card geometry (also used by the card placement logic). */
export const CARD_WIDTH = 320;
export const CARD_GAP = 16;
/** Screen space kept free on the right edge for the zoom controls. */
export const CONTROLS_SPACE = 88;
/** Thickness of the grid rulers along the top / left edge. */
export const RULER_SIZE = 20;

export const OVERVIEW_CAMERA: Camera = { cx: MAP_WIDTH / 2, cy: MAP_HEIGHT / 2, zoom: 1 };

export function fitScale(viewport: Viewport): number {
  if (viewport.width <= 0 || viewport.height <= 0) return 1;
  return Math.max(
    0.05,
    Math.min((viewport.width - FIT_PADDING * 2) / MAP_WIDTH, (viewport.height - FIT_PADDING * 2) / MAP_HEIGHT),
  );
}

export function computeView(camera: Camera, viewport: Viewport): MapView {
  const fit = fitScale(viewport);
  const scale = fit * camera.zoom;
  return {
    fit,
    scale,
    tx: viewport.width / 2 - camera.cx * scale,
    ty: viewport.height / 2 - camera.cy * scale,
  };
}

/** Keeps the zoom in range and the viewport centre over the map. */
export function clampCamera(camera: Camera): Camera {
  return {
    zoom: clamp(camera.zoom, MIN_ZOOM, MAX_ZOOM),
    cx: clamp(camera.cx, 0, MAP_WIDTH),
    cy: clamp(camera.cy, 0, MAP_HEIGHT),
  };
}

export function screenToMap(point: Point, view: MapView): Point {
  return { x: (point.x - view.tx) / view.scale, y: (point.y - view.ty) / view.scale };
}

export function mapToScreen(point: Point, view: MapView): Point {
  return { x: view.tx + point.x * view.scale, y: view.ty + point.y * view.scale };
}

/** Zooms by `factor` while keeping the map point under `screenPoint` fixed. */
export function zoomAround(camera: Camera, viewport: Viewport, screenPoint: Point, factor: number): Camera {
  const view = computeView(camera, viewport);
  const anchor = screenToMap(screenPoint, view);
  const zoom = clamp(camera.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  const scale = view.fit * zoom;
  return {
    zoom,
    cx: anchor.x - (screenPoint.x - viewport.width / 2) / scale,
    cy: anchor.y - (screenPoint.y - viewport.height / 2) / scale,
  };
}

/**
 * Camera that frames a zone: zoom 1.6–3 depending on zone size, centred on the
 * zone. When the info card is shown, zone + card are centred together as a
 * group (or, if the zone is too large for that, the zone is kept on the left
 * so the card does not cover it).
 */
export function cameraForZone(bbox: Rect, centroid: Point, viewport: Viewport, withCard: boolean): Camera {
  const fit = fitScale(viewport);
  const width = Math.max(bbox.width, 12);
  const height = Math.max(bbox.height, 12);
  const left = RULER_SIZE + 12;
  const right = viewport.width - CONTROLS_SPACE;
  const cardSpace = CARD_WIDTH + CARD_GAP;
  const sideBySide = withCard && right - left >= cardSpace + 120;

  let zoom = Math.min((viewport.width * 0.42) / (width * fit), (viewport.height * 0.46) / (height * fit));
  if (sideBySide) zoom = Math.min(zoom, (right - left - cardSpace) / (width * fit));
  zoom = clamp(zoom, FOCUS_MIN_ZOOM, FOCUS_MAX_ZOOM);
  const scale = fit * zoom;

  if (!sideBySide) return clampCamera({ cx: centroid.x, cy: centroid.y, zoom });

  const groupWidth = width * scale + cardSpace;
  const groupLeft =
    groupWidth <= right - left ? clamp((viewport.width - groupWidth) / 2, left, right - groupWidth) : left;
  // Place the zone's left edge at groupLeft: groupLeft = vw/2 + (bbox.x − cx) × scale
  const cx = bbox.x - (groupLeft - viewport.width / 2) / scale;
  return clampCamera({ cx, cy: centroid.y, zoom });
}

export interface MapCamera {
  camera: Camera;
  motion: CameraMotion;
  view: MapView;
  /** Latest camera (also valid between a set call and the next render). */
  getCamera: () => Camera;
  /** Latest measured viewport size. */
  getViewport: () => Viewport;
  setCamera: (camera: Camera, motion: CameraMotion) => void;
  zoomAt: (screenPoint: Point, factor: number, motion: CameraMotion) => void;
  zoomBy: (factor: number) => void;
  panBy: (dx: number, dy: number) => void;
  reset: () => void;
  focusRect: (bbox: Rect, centroid: Point, withCard: boolean) => void;
}

export function useMapCamera(viewport: Viewport): MapCamera {
  const [state, setState] = useState<{ camera: Camera; motion: CameraMotion }>({
    camera: OVERVIEW_CAMERA,
    motion: 'none',
  });
  const cameraRef = useRef<Camera>(OVERVIEW_CAMERA);
  const viewportRef = useRef<Viewport>(viewport);
  const lastSizeRef = useRef<Viewport>(viewport);

  useLayoutEffect(() => {
    viewportRef.current = viewport;
    lastSizeRef.current = viewport;
  });

  const setCamera = useCallback((camera: Camera, motion: CameraMotion) => {
    const next = clampCamera(camera);
    cameraRef.current = next;
    setState({ camera: next, motion });
  }, []);

  const getCamera = useCallback(() => cameraRef.current, []);
  const getViewport = useCallback(() => viewportRef.current, []);

  const zoomAt = useCallback(
    (screenPoint: Point, factor: number, motion: CameraMotion) => {
      setCamera(zoomAround(cameraRef.current, viewportRef.current, screenPoint, factor), motion);
    },
    [setCamera],
  );

  const zoomBy = useCallback(
    (factor: number) => {
      const vp = viewportRef.current;
      zoomAt({ x: vp.width / 2, y: vp.height / 2 }, factor, 'smooth');
    },
    [zoomAt],
  );

  const panBy = useCallback(
    (dx: number, dy: number) => {
      const current = cameraRef.current;
      const scale = computeView(current, viewportRef.current).scale;
      setCamera({ ...current, cx: current.cx + dx / scale, cy: current.cy + dy / scale }, 'quick');
    },
    [setCamera],
  );

  const reset = useCallback(() => setCamera(OVERVIEW_CAMERA, 'smooth'), [setCamera]);

  const focusRect = useCallback(
    (bbox: Rect, centroid: Point, withCard: boolean) => {
      setCamera(cameraForZone(bbox, centroid, viewportRef.current, withCard), 'smooth');
    },
    [setCamera],
  );

  // A viewport resize must not animate — the map would visibly swim.
  const sizeChanged =
    lastSizeRef.current.width !== viewport.width || lastSizeRef.current.height !== viewport.height;
  const motion: CameraMotion = sizeChanged ? 'none' : state.motion;

  const view = useMemo(() => computeView(state.camera, viewport), [state.camera, viewport]);

  return { camera: state.camera, motion, view, getCamera, getViewport, setCamera, zoomAt, zoomBy, panBy, reset, focusRect };
}
