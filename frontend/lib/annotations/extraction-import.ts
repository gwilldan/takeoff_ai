import type { Calibration, Layer, LayerMeasure, PageGuides, Point, Shape } from './types';

/**
 * Maps an extraction result onto annotation layers.
 *
 * The result is QuantTakeoff's takeoff format with our fields beside theirs.
 * Detections are in page pixels of an image of `image_size`; each point is
 * scaled onto the PDF page by `page_size_pt / image_size`, exactly as the
 * annotate-pdf-from-json annotator does, so shapes land where it draws them.
 */

export type Detection = {
  id: string;
  class_name: string;
  score: number;
  bbox: number[];
  polygon: number[][];
  /** A room's name, or a door or window's mark ("Dr-02"), as printed. */
  name?: string | null;
  needs_review?: boolean | null;
  area_m2?: number | null;
  wall_type?: { thickness_mm: number; external?: boolean | null } | null;
  /** Ours: a wall's length from its band and the page scale. */
  length_m?: number;
  /** Ours: the room's area at our scale, where ours replaced QuantTakeoff's estimate. */
  area_m2_checked?: number;
};

export type ResultPage = {
  page_index: number;
  image_size?: { width: number; height: number };
  scale?: { meters_per_pixel: number; denominator?: number | null; source?: string };
  detections: Detection[];
  // Ours.
  is_plan: boolean;
  plan_type?: string;
  skipped_reason?: string;
  error?: string;
  page_size_pt?: { width: number; height: number };
  scale_check?: { text: string; ratio: number; confidence?: string; source?: string };
  grid?: { columns: { ref: string; x: number }[]; rows: { ref: string; y: number }[] };
  metres_per_px?: number | null;
  scale_used?: 'quanttakeoff' | 'ours' | null;
  timings?: { read_s?: number; takeoff_s?: number };
};

export type TakeoffDocument = {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  pages: ResultPage[];
  metrics?: {
    counts?: Record<string, number>;
    wall_length_m?: number;
    room_area_m2?: number;
    seconds?: { read_mean?: number | null; takeoff_mean?: number | null; takeoff_max?: number | null };
  };
  review?: { doors: number; windows: number };
  error?: string | null;
  // Ours.
  document: { page_count: number; pages_processed: number; plan_page_count: number };
  accepted?: boolean;
  rejection?: { reason: string; message: string };
  timings?: { elapsed_s?: number };
};

/**
 * Layer presentation per detection class: the annotator's colours, fill
 * opacities and label sizes. Walls carry no label; their length is the total.
 */
const LAYER_STYLES: Record<string, { label: string; color: string; fillOpacity: number; labelSize?: number; measure?: LayerMeasure }> = {
  rooms: { label: 'Rooms', color: '#17A34A', fillOpacity: 0.25, labelSize: 4 },
  walls: { label: 'Walls', color: '#2663EB', fillOpacity: 0.45, measure: 'band' },
  windows: { label: 'Windows', color: '#0891B3', fillOpacity: 0.7, labelSize: 3, measure: 'count' },
  doors: { label: 'Doors', color: '#EB590D', fillOpacity: 0.7, labelSize: 3, measure: 'count' }
};

const FALLBACK_STYLE = { color: '#7D3BED', fillOpacity: 0.45, labelSize: 3, measure: 'count' as LayerMeasure };

/** Paint order, back to front, as the annotator draws: rooms, walls, windows, doors. */
const LAYER_ORDER = ['rooms', 'walls', 'windows', 'doors'];

const LAYER_FOR_CLASS: Record<string, string> = { space: 'rooms', room: 'rooms', wall: 'walls', door: 'doors', window: 'windows' };

function layerFor(className: string): string {
  return LAYER_FOR_CLASS[className] ?? `${className}s`;
}

/** Points per page pixel, or null when a page has no takeoff geometry. */
function pointsPerPixel(page: ResultPage): { x: number; y: number } | null {
  if (!page.image_size || !page.page_size_pt) {
    return null;
  }

  return {
    x: page.page_size_pt.width / page.image_size.width,
    y: page.page_size_pt.height / page.image_size.height
  };
}

function toShape(detection: Detection, pageNumber: number, ppp: { x: number; y: number }): Shape | null {
  const points: Point[] = detection.polygon
    .filter((pair) => pair.length >= 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]))
    .map(([x, y]) => ({ x: x! * ppp.x, y: y! * ppp.y }));

  if (points.length < 3) {
    return null;
  }

  return {
    id: `${pageNumber}:${detection.id}`,
    kind: 'area',
    pageNumber,
    points,
    componentType: detection.class_name,
    score: detection.score,
    // A room's name is always shown; a door or window's mark on selection.
    ...(detection.name ? (detection.class_name === 'door' || detection.class_name === 'window'
      ? { reference: detection.name }
      : { name: detection.name }) : {}),
    ...(detection.needs_review ? { needsReview: true } : {})
  };
}

/**
 * One layer per detection class across every plan page. Layers span pages — a
 * shape carries its own page number — so a layer's total can be read for the
 * page on screen or for the whole set.
 */
export function layersFromExtraction(result: TakeoffDocument | null | undefined): Layer[] {
  const byLayer = new Map<string, Shape[]>();

  for (const page of result?.pages ?? []) {
    const ppp = pointsPerPixel(page);
    if (!page.is_plan || !ppp) {
      continue;
    }

    for (const detection of page.detections) {
      const shape = toShape(detection, page.page_index + 1, ppp);
      if (!shape) {
        continue;
      }

      const name = layerFor(detection.class_name);
      byLayer.set(name, [...(byLayer.get(name) ?? []), shape]);
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
      kind: 'area' as const,
      visible: true,
      shapes: byLayer.get(name) ?? [],
      source: 'extraction' as const,
      fillOpacity: style.fillOpacity,
      ...(style.labelSize ? { labelSize: style.labelSize } : {}),
      ...(style.measure ? { measure: style.measure } : {})
    };
  });
}

/** True when the worker looked at the document and found no floor plan. */
export function isRejected(result: TakeoffDocument | null | undefined): boolean {
  return result?.accepted === false && Boolean(result?.rejection);
}

/**
 * The drawing scale to adopt: the first plan page's metres per pixel, turned
 * into PDF points per real millimetre. A set is normally drawn at one scale.
 */
export function calibrationFromExtraction(result: TakeoffDocument | null | undefined): Calibration | null {
  for (const page of result?.pages ?? []) {
    const ppp = pointsPerPixel(page);

    if (page.is_plan && ppp && page.metres_per_px) {
      // One point covers 1/ppp pixels, each `metres_per_px` of real world.
      return { pointsPerMillimetre: ppp.x / (page.metres_per_px * 1000) };
    }
  }

  return null;
}

/** Snap targets per page: the drawing's grid axes and the corners of its detections. */
export function guidesFromExtraction(result: TakeoffDocument | null | undefined): Record<number, PageGuides> {
  const guides: Record<number, PageGuides> = {};

  for (const page of result?.pages ?? []) {
    const ppp = pointsPerPixel(page);
    if (!page.is_plan || !ppp) {
      continue;
    }

    guides[page.page_index + 1] = {
      xs: (page.grid?.columns ?? []).map((axis) => axis.x * ppp.x),
      ys: (page.grid?.rows ?? []).map((axis) => axis.y * ppp.y),
      points: page.detections
        .filter((d) => d.class_name === 'wall')
        .flatMap((d) => d.polygon.map(([x, y]) => ({ x: x! * ppp.x, y: y! * ppp.y })))
    };
  }

  return guides;
}
