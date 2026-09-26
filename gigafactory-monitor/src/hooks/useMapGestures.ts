import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { Point } from '../types/zones';
import {
  MAX_ZOOM,
  MIN_ZOOM,
  computeView,
  fitScale,
  screenToMap,
  type Camera,
  type CameraMotion,
  type Viewport,
} from './useMapCamera';
import { clamp } from '../utils/geometry';

/**
 * Pointer gestures for the map viewport:
 *   - tap (mouse click / finger tap)  → onTap with the element that was hit
 *   - drag with one pointer           → pan
 *   - pinch with two fingers          → zoom around the fingers
 *   - mouse wheel / trackpad pinch    → zoom around the cursor
 *
 * Elements marked with `data-map-ui` (controls, cards, panels) are ignored so
 * their own buttons keep working.
 */

export interface GestureHandlers {
  getCamera: () => Camera;
  getViewport: () => Viewport;
  setCamera: (camera: Camera, motion: CameraMotion) => void;
  zoomAt: (screenPoint: Point, factor: number, motion: CameraMotion) => void;
  onTap: (screenPoint: Point, target: Element | null) => void;
}

const DRAG_THRESHOLD = 6;

type Gesture =
  | { kind: 'pan'; pointerId: number; start: Point; startCamera: Camera; moved: boolean; target: Element | null }
  | { kind: 'pinch'; startDistance: number; startMid: Point; startCamera: Camera };

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function isMapUi(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('[data-map-ui]') !== null;
}

export function useMapGestures(ref: RefObject<HTMLElement>, handlers: GestureHandlers): { dragging: boolean } {
  const handlersRef = useRef(handlers);
  const [dragging, setDragging] = useState(false);

  useLayoutEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const pointers = new Map<number, Point>();
    let gesture: Gesture | null = null;
    let tapCancelled = false;

    const toLocal = (event: { clientX: number; clientY: number }): Point => {
      const rect = element.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const startPinch = () => {
      const [a, b] = Array.from(pointers.values());
      gesture = {
        kind: 'pinch',
        startDistance: Math.max(1, distance(a, b)),
        startMid: midpoint(a, b),
        startCamera: handlersRef.current.getCamera(),
      };
      tapCancelled = true;
      setDragging(true);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      if (isMapUi(event.target)) return;
      const point = toLocal(event);
      pointers.set(event.pointerId, point);

      if (pointers.size === 1) {
        tapCancelled = false;
        gesture = {
          kind: 'pan',
          pointerId: event.pointerId,
          start: point,
          startCamera: handlersRef.current.getCamera(),
          moved: false,
          target: event.target instanceof Element ? event.target : null,
        };
      } else if (pointers.size === 2) {
        for (const id of pointers.keys()) {
          try {
            element.setPointerCapture(id);
          } catch {
            /* pointer may already be gone */
          }
        }
        startPinch();
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      if (!pointers.has(event.pointerId) || !gesture) return;
      pointers.set(event.pointerId, toLocal(event));
      const { getViewport, setCamera } = handlersRef.current;

      if (gesture.kind === 'pan') {
        if (event.pointerId !== gesture.pointerId) return;
        const current = pointers.get(gesture.pointerId);
        if (!current) return;
        const dx = current.x - gesture.start.x;
        const dy = current.y - gesture.start.y;
        if (!gesture.moved) {
          if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
          gesture.moved = true;
          tapCancelled = true;
          try {
            element.setPointerCapture(event.pointerId);
          } catch {
            /* ignore */
          }
          setDragging(true);
        }
        const scale = computeView(gesture.startCamera, getViewport()).scale;
        setCamera(
          { ...gesture.startCamera, cx: gesture.startCamera.cx - dx / scale, cy: gesture.startCamera.cy - dy / scale },
          'none',
        );
        return;
      }

      if (pointers.size < 2) return;
      const [a, b] = Array.from(pointers.values());
      const viewport = getViewport();
      const anchor = screenToMap(gesture.startMid, computeView(gesture.startCamera, viewport));
      const zoom = clamp((gesture.startCamera.zoom * distance(a, b)) / gesture.startDistance, MIN_ZOOM, MAX_ZOOM);
      const scale = fitScale(viewport) * zoom;
      const mid = midpoint(a, b);
      setCamera(
        {
          zoom,
          cx: anchor.x - (mid.x - viewport.width / 2) / scale,
          cy: anchor.y - (mid.y - viewport.height / 2) / scale,
        },
        'none',
      );
    };

    const onPointerEnd = (event: PointerEvent) => {
      if (!pointers.has(event.pointerId)) return;
      const point = toLocal(event);
      pointers.delete(event.pointerId);

      if (gesture?.kind === 'pan' && gesture.pointerId === event.pointerId) {
        if (!gesture.moved && !tapCancelled && event.type === 'pointerup') {
          handlersRef.current.onTap(point, gesture.target);
        }
        gesture = null;
      } else if (gesture?.kind === 'pinch' && pointers.size === 1) {
        // Continue as a pan with the remaining finger.
        const [[id, remaining]] = Array.from(pointers.entries());
        gesture = {
          kind: 'pan',
          pointerId: id,
          start: remaining,
          startCamera: handlersRef.current.getCamera(),
          moved: true,
          target: null,
        };
      } else if (gesture?.kind === 'pinch' && pointers.size >= 2) {
        startPinch();
      }

      if (pointers.size === 0) {
        gesture = null;
        setDragging(false);
      }
    };

    const onWheel = (event: WheelEvent) => {
      if (isMapUi(event.target)) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
      const delta = clamp(event.deltaY * unit, -240, 240);
      // Trackpad pinch arrives as ctrl+wheel with small deltas.
      const factor = Math.exp(-delta * (event.ctrlKey ? 0.01 : 0.0022));
      handlersRef.current.zoomAt(toLocal(event), factor, 'quick');
    };

    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', onPointerEnd);
    element.addEventListener('pointercancel', onPointerEnd);
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      element.removeEventListener('pointerdown', onPointerDown);
      element.removeEventListener('pointermove', onPointerMove);
      element.removeEventListener('pointerup', onPointerEnd);
      element.removeEventListener('pointercancel', onPointerEnd);
      element.removeEventListener('wheel', onWheel);
    };
  }, [ref]);

  return { dragging };
}
