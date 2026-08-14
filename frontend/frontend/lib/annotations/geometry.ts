import type { Point, Shape } from './types';

export function distance(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** Total length of an open polyline. Returns 0 for fewer than 2 points. */
export function polylineLength(points: Point[]): number {
  let total = 0;

  for (let index = 1; index < points.length; index += 1) {
    total += distance(points[index - 1]!, points[index]!);
  }

  return total;
}

/**
 * Area of an implicitly closed polygon via the shoelace formula.
 * Absolute value, so winding direction does not matter.
 * Returns 0 for fewer than 3 points.
 */
export function polygonArea(points: Point[]): number {
  if (points.length < 3) {
    return 0;
  }

  let sum = 0;

  for (let index = 0; index < points.length; index += 1) {
    const current = points[index]!;
    const next = points[(index + 1) % points.length]!;
    sum += current.x * next.y - next.x * current.y;
  }

  return Math.abs(sum) / 2;
}

/** Shortest distance from `p` to the segment `a`–`b`. */
export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;

  if (lengthSquared === 0) {
    return distance(p, a);
  }

  // Projection of ap onto ab, clamped to the segment.
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSquared));

  return distance(p, { x: a.x + t * dx, y: a.y + t * dy });
}

/** Ray casting test against an implicitly closed polygon. */
export function isPointInPolygon(p: Point, polygon: Point[]): boolean {
  if (polygon.length < 3) {
    return false;
  }

  let inside = false;

  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i]!;
    const b = polygon[j]!;

    const straddlesRay = a.y > p.y !== b.y > p.y;
    if (!straddlesRay) {
      continue;
    }

    const crossingX = a.x + ((p.y - a.y) / (b.y - a.y)) * (b.x - a.x);
    if (p.x < crossingX) {
      inside = !inside;
    }
  }

  return inside;
}

/**
 * Whether a pointer at `p` should select `shape`.
 * Lines hit near any segment; areas hit from inside or near any edge,
 * including the closing edge.
 */
export function hitTestShape(shape: Shape, p: Point, tolerancePts: number): boolean {
  const { points } = shape;

  if (points.length === 0) {
    return false;
  }

  if (points.length === 1) {
    return distance(p, points[0]!) <= tolerancePts;
  }

  if (shape.kind === 'area' && isPointInPolygon(p, points)) {
    return true;
  }

  const segmentCount = shape.kind === 'area' ? points.length : points.length - 1;

  for (let index = 0; index < segmentCount; index += 1) {
    const a = points[index]!;
    const b = points[(index + 1) % points.length]!;

    if (distanceToSegment(p, a, b) <= tolerancePts) {
      return true;
    }
  }

  return false;
}

export function boundingBox(
  points: Point[]
): { minX: number; minY: number; maxX: number; maxY: number } | null {
  const first = points[0];

  if (!first) {
    return null;
  }

  let minX = first.x;
  let minY = first.y;
  let maxX = first.x;
  let maxY = first.y;

  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }

  return { minX, minY, maxX, maxY };
}
