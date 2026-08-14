import type { Layer, Point, Shape, ShapeKind } from './types';

/**
 * Maps a worker extraction result onto annotation layers.
 *
 * The worker reports coordinates in PDF points with a top-left origin, which is
 * the same space the overlay draws in, so points are copied across untouched —
 * no transform, no scaling. If annotations ever land off the drawing, that
 * assumption is the first thing to check.
 */

export type ExtractedComponent = {
  id: string;
  layer: string;
  type: string;
  polyline: number[][];
  confidence?: string;
  reference?: string | null;
  gridRef?: string | null;
  gridRefFrom?: string | null;
  gridRefTo?: string | null;
  lengthMm?: number;
  widthMm?: number;
  thicknessMm?: number;
  areaM2?: number | null;
  hostWallId?: string | null;
  name?: string | null;
  notes?: string[];
};

export type ExtractedPage = {
  pageNumber: number;
  isPlan: boolean;
  planType?: string;
  skippedReason?: string;
  pageSize?: { width: number; height: number };
  scale?: { text: string; ratio: number; confidence?: string };
  grid?: { columns: { ref: string; x: number }[]; rows: { ref: string; y: number }[] };
  components?: ExtractedComponent[];
  counts?: Record<string, number>;
  notes?: string[];
};

export type ExtractionDocument = {
  accepted?: boolean;
  partial?: boolean;
  document?: { pageCount?: number; pagesProcessed?: number; planPageCount?: number };
  pages?: ExtractedPage[];
  totals?: Record<string, number>;
  rejection?: { reason: string; message: string };
  tokenUsage?: { totals?: Record<string, number> };
  extractedAt?: string;
};

/**
 * Layer presentation per component class.
 *
 * Openings are lines because that is what they are geometrically — a span across
 * the opening whose length is its width. Keeping them as lines means the overlay
 * renderer needs no new shape type.
 */
const LAYER_STYLES: Record<string, { label: string; color: string; kind: ShapeKind }> = {
  walls: { label: 'Walls', color: '#2563EB', kind: 'line' },
  doors: { label: 'Doors', color: '#EA580C', kind: 'line' },
  windows: { label: 'Windows', color: '#0891B2', kind: 'line' },
  columns: { label: 'Columns', color: '#DB2777', kind: 'area' },
  rooms: { label: 'Rooms', color: '#16A34A', kind: 'area' }
};

const FALLBACK_STYLE = { color: '#7C3AED', kind: 'line' as ShapeKind };

/** Order layers the way a drawing reads: structure first, then openings, then space. */
const LAYER_ORDER = ['walls', 'columns', 'doors', 'windows', 'rooms'];

function toPoints(polyline: number[][]): Point[] {
  return polyline
    .filter((pair) => pair.length >= 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]))
    .map((pair) => ({ x: pair[0]!, y: pair[1]! }));
}

function gridRefFor(component: ExtractedComponent): string | undefined {
  if (component.gridRef) {
    return component.gridRef;
  }

  if (component.gridRefFrom && component.gridRefTo) {
    return `${component.gridRefFrom} → ${component.gridRefTo}`;
  }

  return component.gridRefFrom ?? undefined;
}

function toShape(component: ExtractedComponent, pageNumber: number, kind: ShapeKind): Shape | null {
  const points = toPoints(component.polyline ?? []);
  const minimum = kind === 'area' ? 3 : 2;

  if (points.length < minimum) {
    return null;
  }

  const shape: Shape = {
    id: `${pageNumber}:${component.id}`,
    kind,
    pageNumber,
    points,
    componentType: component.type
  };

  if (component.reference) {
    shape.reference = component.reference;
  }

  if (component.name) {
    shape.name = component.name;
  }

  const gridRef = gridRefFor(component);
  if (gridRef) {
    shape.gridRef = gridRef;
  }

  return shape;
}

/**
 * Build one layer per component class across every plan page in the result.
 *
 * Layers span pages — a shape carries its own page number, so a single "Walls"
 * layer holds the walls of the whole set and its total is the document total.
 */
export type ImportOptions = {
  /**
   * Component classes to import. Omit to take everything the extraction found.
   *
   * The workspace currently passes `['rooms']`: rooms are anchored on the
   * drawing's own printed area tags and are reliable across drawing styles,
   * whereas wall reconstruction depends on a convention not every drawing
   * follows. Widen this list as each class earns its place.
   */
  only?: string[];
};

export function layersFromExtraction(
  result: ExtractionDocument | null | undefined,
  options: ImportOptions = {}
): Layer[] {
  const byLayer = new Map<string, Shape[]>();
  const allowed = options.only ? new Set(options.only) : null;

  for (const page of result?.pages ?? []) {
    if (!page.isPlan) {
      continue;
    }

    for (const component of page.components ?? []) {
      if (allowed && !allowed.has(component.layer)) {
        continue;
      }

      const style = LAYER_STYLES[component.layer] ?? FALLBACK_STYLE;
      const shape = toShape(component, page.pageNumber, style.kind);

      if (!shape) {
        continue;
      }

      const existing = byLayer.get(component.layer);
      if (existing) {
        existing.push(shape);
      } else {
        byLayer.set(component.layer, [shape]);
      }
    }
  }

  const known = LAYER_ORDER.filter((name) => byLayer.has(name));
  const extra = [...byLayer.keys()].filter((name) => !LAYER_ORDER.includes(name)).sort();

  return [...known, ...extra].map((name) => {
    const style = LAYER_STYLES[name] ?? FALLBACK_STYLE;

    return {
      id: `extraction:${name}`,
      name: LAYER_STYLES[name]?.label ?? name.replace(/^./, (c) => c.toUpperCase()),
      color: style.color,
      kind: style.kind,
      visible: true,
      shapes: byLayer.get(name) ?? [],
      source: 'extraction' as const
    };
  });
}

/** True when the worker looked at the document and found no measurable plan. */
export function isRejected(result: ExtractionDocument | null | undefined): boolean {
  return result?.accepted === false && Boolean(result?.rejection);
}

/** Pages the worker examined and skipped, for reporting alongside the results. */
export function skippedPages(result: ExtractionDocument | null | undefined): ExtractedPage[] {
  return (result?.pages ?? []).filter((page) => !page.isPlan);
}

/**
 * The drawing scale to adopt from an extraction.
 *
 * Taken from the first plan page that reports a usable ratio: a set is normally
 * drawn at one scale, and a wrong scale is more obvious than no scale.
 */
export function calibrationFromExtraction(
  result: ExtractionDocument | null | undefined
): { pointsPerMillimetre: number } | null {
  for (const page of result?.pages ?? []) {
    const ratio = page.scale?.ratio;

    if (page.isPlan && typeof ratio === 'number' && ratio > 0) {
      // One PDF point is 25.4/72 mm of paper, covering `ratio` mm of real world.
      return { pointsPerMillimetre: 1 / ((25.4 / 72) * ratio) };
    }
  }

  return null;
}
