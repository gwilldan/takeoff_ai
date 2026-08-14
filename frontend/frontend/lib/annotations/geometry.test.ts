import { describe, expect, it } from 'vitest';
import {
  boundingBox,
  distance,
  distanceToSegment,
  hitTestShape,
  isPointInPolygon,
  polygonArea,
  polylineLength
} from './geometry';
import type { Shape } from './types';

const SQUARE = [
  { x: 0, y: 0 },
  { x: 10, y: 0 },
  { x: 10, y: 10 },
  { x: 0, y: 10 }
];

function lineShape(points: { x: number; y: number }[]): Shape {
  return { id: 's1', kind: 'line', pageNumber: 1, points };
}

function areaShape(points: { x: number; y: number }[]): Shape {
  return { id: 's2', kind: 'area', pageNumber: 1, points };
}

describe('distance', () => {
  it('measures a 3-4-5 triangle hypotenuse', () => {
    expect(distance({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
  });

  it('is zero for identical points', () => {
    expect(distance({ x: 2, y: 2 }, { x: 2, y: 2 })).toBe(0);
  });
});

describe('polylineLength', () => {
  it('sums every segment', () => {
    expect(polylineLength([{ x: 0, y: 0 }, { x: 3, y: 0 }, { x: 3, y: 4 }])).toBe(7);
  });

  it('is zero for fewer than two points', () => {
    expect(polylineLength([])).toBe(0);
    expect(polylineLength([{ x: 5, y: 5 }])).toBe(0);
  });

  it('does not close the path', () => {
    expect(polylineLength(SQUARE)).toBe(30);
  });
});

describe('polygonArea', () => {
  it('measures a square', () => {
    expect(polygonArea(SQUARE)).toBe(100);
  });

  it('ignores winding direction', () => {
    expect(polygonArea([...SQUARE].reverse())).toBe(100);
  });

  it('measures a non-convex L shape', () => {
    // 10x10 square with a 5x5 bite taken out of the top-right corner.
    const lShape = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 5 },
      { x: 5, y: 5 },
      { x: 5, y: 10 },
      { x: 0, y: 10 }
    ];
    expect(polygonArea(lShape)).toBe(75);
  });

  it('is zero for fewer than three points', () => {
    expect(polygonArea([{ x: 0, y: 0 }, { x: 1, y: 1 }])).toBe(0);
  });
});

describe('distanceToSegment', () => {
  it('measures perpendicular distance when the foot lies on the segment', () => {
    expect(distanceToSegment({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(3);
  });

  it('falls back to the nearer endpoint when the foot lies beyond the segment', () => {
    expect(distanceToSegment({ x: 14, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(4);
  });

  it('handles a degenerate zero-length segment', () => {
    expect(distanceToSegment({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 })).toBe(5);
  });
});

describe('isPointInPolygon', () => {
  it('accepts an interior point', () => {
    expect(isPointInPolygon({ x: 5, y: 5 }, SQUARE)).toBe(true);
  });

  it('rejects an exterior point', () => {
    expect(isPointInPolygon({ x: 15, y: 5 }, SQUARE)).toBe(false);
  });

  it('rejects a point inside the removed notch of an L shape', () => {
    const lShape = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 5 },
      { x: 5, y: 5 },
      { x: 5, y: 10 },
      { x: 0, y: 10 }
    ];
    expect(isPointInPolygon({ x: 8, y: 8 }, lShape)).toBe(false);
    expect(isPointInPolygon({ x: 2, y: 8 }, lShape)).toBe(true);
  });
});

describe('hitTestShape', () => {
  it('hits a line within tolerance of a segment', () => {
    expect(hitTestShape(lineShape(SQUARE), { x: 5, y: 2 }, 4)).toBe(true);
  });

  it('misses a line outside tolerance', () => {
    // SQUARE as a polyline is an open C: bottom y=0, right x=10, top y=10.
    // (5,5) sits 5 from each of those, clear of a tolerance of 4 — and it is
    // the same point that hits when the shape is an area, which is the
    // difference between the two kinds.
    expect(hitTestShape(lineShape(SQUARE), { x: 5, y: 5 }, 4)).toBe(false);
  });

  it('hits an area from its interior', () => {
    expect(hitTestShape(areaShape(SQUARE), { x: 5, y: 5 }, 4)).toBe(true);
  });

  it('hits an area from just outside its edge, within tolerance', () => {
    expect(hitTestShape(areaShape(SQUARE), { x: 12, y: 5 }, 4)).toBe(true);
  });

  it('misses an area well outside it', () => {
    expect(hitTestShape(areaShape(SQUARE), { x: 30, y: 5 }, 4)).toBe(false);
  });
});

describe('boundingBox', () => {
  it('bounds a point set', () => {
    expect(boundingBox([{ x: 2, y: 8 }, { x: -1, y: 3 }])).toEqual({
      minX: -1,
      minY: 3,
      maxX: 2,
      maxY: 8
    });
  });

  it('returns null for an empty set', () => {
    expect(boundingBox([])).toBeNull();
  });
});
