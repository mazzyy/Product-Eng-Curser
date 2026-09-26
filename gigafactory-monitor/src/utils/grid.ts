import { MAP_HEIGHT, MAP_WIDTH } from '../config/factoryZones';
import type { Point } from '../types/zones';

/**
 * Site-plan grid reference (A–L columns × 1–9 rows, ~120 px square cells on the
 * 1448 × 1086 plan), drawn as rulers along the map edges. Gives engineers a
 * spoken reference ("Casting, grid D8").
 */
export const GRID_COLS = 12;
export const GRID_ROWS = 9;
export const CELL_WIDTH = MAP_WIDTH / GRID_COLS;
export const CELL_HEIGHT = MAP_HEIGHT / GRID_ROWS;

export function columnLabel(index: number): string {
  return String.fromCharCode(65 + index);
}

export function gridCell(point: Point): { col: number; row: number } {
  return {
    col: Math.min(GRID_COLS - 1, Math.max(0, Math.floor(point.x / CELL_WIDTH))),
    row: Math.min(GRID_ROWS - 1, Math.max(0, Math.floor(point.y / CELL_HEIGHT))),
  };
}

export function gridRef(point: Point): string {
  const { col, row } = gridCell(point);
  return `${columnLabel(col)}${row + 1}`;
}
