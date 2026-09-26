import { FACTORY_ZONES } from '../config/factoryZones';
import type { Point } from '../types/zones';
import { boundingBox, parsePolygon, polygonToPath, type Rect } from './geometry';

export interface ZoneGeometry {
  points: Point[];
  bbox: Rect;
  path: string;
}

/** Pre-computed geometry for every configured zone (computed once at load). */
export const ZONE_GEOMETRY: Record<string, ZoneGeometry> = Object.fromEntries(
  FACTORY_ZONES.map((zone) => {
    const points = parsePolygon(zone.polygon);
    return [zone.id, { points, bbox: boundingBox(points), path: polygonToPath(points) }];
  }),
);

export function getZoneGeometry(zoneId: string): ZoneGeometry | undefined {
  return ZONE_GEOMETRY[zoneId];
}
