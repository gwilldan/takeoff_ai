import { distanceToSegment } from './geometry';
import type { PageGuides, Point, Shape } from './types';

export type SnapResult = {
  point: Point;
  /** What the point snapped to, for the on-canvas indicator. */
  snappedX: boolean;
  snappedY: boolean;
};

/**
 * Pull a dragged vertex onto nearby targets, strongest first:
 *
 * 1. a point target — an extracted node or another shape's vertex — within
 *    tolerance wins outright, so a corner lands exactly on the drawing's corner;
 * 2. otherwise each axis snaps independently to a grid line, another vertex's
 *    coordinate, or the dragged vertex's own neighbours, which keeps a wall
 *    edge straight while its length changes.
 *
 * Everything is in PDF points; `tolerance` is already divided by the zoom.
 */
export function snapPoint(
  target: Point,
  tolerance: number,
  options: {
    guides?: PageGuides;
    shapes: Shape[];
    /** The shape being edited and the index of the vertex being dragged. */
    editing: { shape: Shape; vertexIndex: number };
  }
): SnapResult {
  const { guides, shapes, editing } = options;

  const pointTargets: Point[] = [...(guides?.points ?? [])];
  const xs: number[] = [...(guides?.xs ?? [])];
  const ys: number[] = [...(guides?.ys ?? [])];

  for (const shape of shapes) {
    for (const [index, vertex] of shape.points.entries()) {
      if (shape.id === editing.shape.id && index === editing.vertexIndex) {
        continue;
      }

      pointTargets.push(vertex);
      xs.push(vertex.x);
      ys.push(vertex.y);
    }
  }

  let best: Point | null = null;
  let bestDistance = tolerance;

  for (const candidate of pointTargets) {
    const d = Math.hypot(candidate.x - target.x, candidate.y - target.y);
    if (d <= bestDistance) {
      best = candidate;
      bestDistance = d;
    }
  }

  if (best) {
    return { point: { x: best.x, y: best.y }, snappedX: true, snappedY: true };
  }

  const nearest = (values: number[], value: number): number | null => {
    let found: number | null = null;
    let gap = tolerance;

    for (const candidate of values) {
      const d = Math.abs(candidate - value);
      if (d <= gap) {
        found = candidate;
        gap = d;
      }
    }

    return found;
  };

  const x = nearest(xs, target.x);
  const y = nearest(ys, target.y);

  return {
    point: { x: x ?? target.x, y: y ?? target.y },
    snappedX: x !== null,
    snappedY: y !== null
  };
}

/** Index of the vertex under `point`, or -1. */
export function vertexAt(shape: Shape, point: Point, tolerance: number): number {
  return shape.points.findIndex(
    (vertex) => Math.hypot(vertex.x - point.x, vertex.y - point.y) <= tolerance
  );
}

/**
 * Where on the shape's outline `point` falls: the index of the edge's start
 * vertex, or -1 when the point is not on an edge. Areas include the closing
 * edge; lines do not.
 */
export function edgeAt(shape: Shape, point: Point, tolerance: number): number {
  const count = shape.kind === 'area' ? shape.points.length : shape.points.length - 1;

  for (let index = 0; index < count; index += 1) {
    const a = shape.points[index]!;
    const b = shape.points[(index + 1) % shape.points.length]!;

    if (distanceToSegment(point, a, b) <= tolerance) {
      return index;
    }
  }

  return -1;
}
