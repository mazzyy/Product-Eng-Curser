export interface Point {
  x: number;
  y: number;
}

/**
 * A monitored factory area drawn as an SVG polygon on top of the site map.
 *
 * Coordinates are in "map units": the pixel grid of the original site-plan
 * image (see MAP_WIDTH / MAP_HEIGHT in src/config/factoryZones.ts).
 */
export interface FactoryZone {
  /** Stable identifier used by alerts and future backend events. */
  id: string;
  /** Building / area code printed on the site plan, e.g. "A103". */
  code: string;
  /** Display name, e.g. "Body in White". */
  name: string;
  /** Optional shorter name for tight spaces (dropdowns, markers). */
  shortName?: string;
  /** Building group used to organise dropdowns and lists. */
  hall: string;
  /** SVG polygon points: "x1,y1 x2,y2 x3,y3 ..." in map units. */
  polygon: string;
  /** Anchor for markers, labels and zoom targeting (map units). */
  centroid: Point;
  /** Fictional equipment located in this zone (used for generated alerts). */
  assets: string[];
}
