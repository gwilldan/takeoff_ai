export type Point = { x: number; y: number };

export type ShapeKind = 'line' | 'area';

/**
 * A drawn annotation. `points` are in PDF point space at scale 1, origin
 * top-left — never screen pixels — so zoom and device pixel ratio cannot
 * change the stored geometry.
 *
 * A `line` shape is an open polyline with at least 2 points.
 * An `area` shape is an implicitly closed polygon with at least 3 points;
 * the closing segment from the last point back to the first is not stored.
 */
export type Shape = {
  id: string;
  kind: ShapeKind;
  pageNumber: number;
  points: Point[];
  /**
   * The drawing's own mark for this element, e.g. "Dr-01" — carried straight
   * through from the extraction so a component can be found on the paper.
   */
  reference?: string;
  /** What the extraction called this: "wall", "door", "window", "room". */
  componentType?: string;
  /** Human name where the drawing gives one, e.g. a room's "BEDROOM". */
  name?: string;
  /** The extraction's confidence, 0–1. */
  score?: number;
  /** The extraction flagged this for a person to check. */
  needsReview?: boolean;
};

/**
 * Where a layer came from. Extracted layers stay fully editable — correcting
 * what the agent got wrong is the whole review workflow — but they are badged,
 * so it stays clear which numbers a person put there and which the agent did.
 */
export type LayerSource = 'user' | 'extraction';

/**
 * What a layer's total adds up. By default a line layer totals length and an
 * area layer area; `count` counts its shapes (doors, windows) and `band`
 * totals the long side of each band (a wall's length).
 */
export type LayerMeasure = 'count' | 'band';

export type Layer = {
  id: string;
  name: string;
  color: string;
  kind: ShapeKind;
  visible: boolean;
  shapes: Shape[];
  source?: LayerSource;
  measure?: LayerMeasure;
  /** Area fill opacity; extracted layers carry the annotator's. */
  fillOpacity?: number;
  /** Label each shape "class score" at this size in PDF points. */
  labelSize?: number;
};

/** Scale calibration for the whole document. */
export type Calibration = {
  pointsPerMillimetre: number;
};

/**
 * Snap targets the extraction found on one page: the drawing's grid axes and
 * the corner nodes of extracted components. Editing a shape snaps to these,
 * which is what lets a hand correction land exactly where the drawing says.
 */
export type PageGuides = {
  xs: number[];
  ys: number[];
  points: Point[];
};

export type AnnotationState = {
  documentKey: string | null;
  layers: Layer[];
  activeLayerId: string | null;
  selectedShapeId: string | null;
  calibration: Calibration | null;
  /** Keyed by page number. Absent before any extraction has been imported. */
  guides?: Record<number, PageGuides>;
};

export type ToolId = 'select' | 'line' | 'area' | 'pan' | 'calibrate';

/** Layer colours, assigned round-robin as layers are created. */
export const LAYER_PALETTE = [
  '#2563EB',
  '#16A34A',
  '#EA580C',
  '#DB2777',
  '#7C3AED',
  '#0891B2',
  '#CA8A04',
  '#DC2626'
] as const;

/**
 * How near a pointer must land to select a shape, in *screen pixels*. Callers
 * divide by the current zoom to get a tolerance in PDF points, so the grab
 * area stays the same size on screen at every zoom level.
 */
export const HIT_TOLERANCE_PX = 5;

/** How near a dragged vertex must come to a snap target, in screen pixels. */
export const SNAP_TOLERANCE_PX = 8;
