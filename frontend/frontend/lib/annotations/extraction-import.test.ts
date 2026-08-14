import { describe, expect, it } from 'vitest';
import {
  calibrationFromExtraction,
  isRejected,
  layersFromExtraction,
  skippedPages,
  type ExtractionDocument
} from './extraction-import';

function planPage(components: unknown[], pageNumber = 1, ratio = 100): ExtractionDocument {
  return {
    accepted: true,
    pages: [
      {
        pageNumber,
        isPlan: true,
        planType: 'ground_floor_plan',
        scale: { text: '1:100', ratio },
        components: components as never[]
      }
    ]
  };
}

const WALL = {
  id: 'w-001',
  layer: 'walls',
  type: 'wall',
  polyline: [
    [100, 100],
    [400, 100]
  ],
  gridRefFrom: 'A/1',
  gridRefTo: 'C/1',
  lengthMm: 10583,
  thicknessMm: 230
};

const DOOR = {
  id: 'd-001',
  layer: 'doors',
  type: 'door',
  polyline: [
    [160, 100],
    [186, 100]
  ],
  reference: 'Dr-01',
  gridRef: 'B/1',
  widthMm: 900
};

const ROOM = {
  id: 'room-001',
  layer: 'rooms',
  type: 'room',
  polyline: [
    [180, 140],
    [220, 140],
    [220, 180],
    [180, 180]
  ],
  name: 'BEDROOM'
};

describe('layersFromExtraction', () => {
  it('creates one layer per component class', () => {
    const layers = layersFromExtraction(planPage([WALL, DOOR, ROOM]));

    expect(layers.map((l) => l.name)).toEqual(['Walls', 'Doors', 'Rooms']);
  });

  it('marks every layer as extracted', () => {
    const layers = layersFromExtraction(planPage([WALL]));

    expect(layers[0]!.source).toBe('extraction');
  });

  it('copies coordinates through untouched', () => {
    const layers = layersFromExtraction(planPage([WALL]));

    expect(layers[0]!.shapes[0]!.points).toEqual([
      { x: 100, y: 100 },
      { x: 400, y: 100 }
    ]);
  });

  it('keeps openings as lines and closed elements as areas', () => {
    const layers = layersFromExtraction(planPage([WALL, DOOR, ROOM]));
    const byName = new Map(layers.map((l) => [l.name, l]));

    expect(byName.get('Walls')!.kind).toBe('line');
    expect(byName.get('Doors')!.kind).toBe('line');
    expect(byName.get('Rooms')!.kind).toBe('area');
  });

  it('carries the drawing reference through', () => {
    const layers = layersFromExtraction(planPage([DOOR]));

    expect(layers[0]!.shapes[0]!.reference).toBe('Dr-01');
    expect(layers[0]!.shapes[0]!.componentType).toBe('door');
  });

  it('joins a wall run’s two grid references', () => {
    const layers = layersFromExtraction(planPage([WALL]));

    expect(layers[0]!.shapes[0]!.gridRef).toBe('A/1 → C/1');
  });

  it('namespaces shape ids by page so two pages cannot collide', () => {
    const result: ExtractionDocument = {
      accepted: true,
      pages: [
        { pageNumber: 1, isPlan: true, components: [WALL] as never[] },
        { pageNumber: 2, isPlan: true, components: [WALL] as never[] }
      ]
    };

    const shapes = layersFromExtraction(result)[0]!.shapes;

    expect(shapes.map((s) => s.id)).toEqual(['1:w-001', '2:w-001']);
    expect(shapes.map((s) => s.pageNumber)).toEqual([1, 2]);
  });

  it('gathers one layer across every page of the set', () => {
    const result: ExtractionDocument = {
      accepted: true,
      pages: [
        { pageNumber: 1, isPlan: true, components: [WALL] as never[] },
        { pageNumber: 2, isPlan: true, components: [WALL] as never[] }
      ]
    };

    const layers = layersFromExtraction(result);

    expect(layers).toHaveLength(1);
    expect(layers[0]!.shapes).toHaveLength(2);
  });

  it('skips pages that are not plans', () => {
    const result: ExtractionDocument = {
      accepted: true,
      pages: [
        { pageNumber: 1, isPlan: false, skippedReason: 'schedule', components: [WALL] as never[] }
      ]
    };

    expect(layersFromExtraction(result)).toEqual([]);
  });

  it('drops components without enough points to draw', () => {
    const result = planPage([
      { id: 'w-002', layer: 'walls', type: 'wall', polyline: [[100, 100]] },
      { id: 'room-002', layer: 'rooms', type: 'room', polyline: [[1, 1], [2, 2]] }
    ]);

    expect(layersFromExtraction(result)).toEqual([]);
  });

  it('orders layers the way a drawing reads', () => {
    const layers = layersFromExtraction(
      planPage([
        ROOM,
        DOOR,
        WALL,
        { id: 'col-1', layer: 'columns', type: 'column', polyline: [[0, 0], [4, 0], [4, 4], [0, 4]] },
        { id: 'win-1', layer: 'windows', type: 'window', polyline: [[200, 100], [230, 100]] }
      ])
    );

    expect(layers.map((l) => l.name)).toEqual([
      'Walls',
      'Columns',
      'Doors',
      'Windows',
      'Rooms'
    ]);
  });

  it('accepts an unknown component class rather than dropping it', () => {
    const layers = layersFromExtraction(
      planPage([{ id: 's-1', layer: 'stairs', type: 'stair', polyline: [[0, 0], [10, 0]] }])
    );

    expect(layers).toHaveLength(1);
    expect(layers[0]!.name).toBe('Stairs');
  });

  it('handles a missing or empty result', () => {
    expect(layersFromExtraction(null)).toEqual([]);
    expect(layersFromExtraction(undefined)).toEqual([]);
    expect(layersFromExtraction({})).toEqual([]);
  });
});

describe('isRejected', () => {
  it('detects a document with no plan', () => {
    expect(
      isRejected({
        accepted: false,
        rejection: { reason: 'not_a_construction_plan', message: 'no' }
      })
    ).toBe(true);
  });

  it('is false for an accepted document', () => {
    expect(isRejected(planPage([WALL]))).toBe(false);
  });

  it('is false while a result is still partial', () => {
    expect(isRejected({ partial: true, pages: [] })).toBe(false);
  });
});

describe('skippedPages', () => {
  it('returns only the pages that were not plans', () => {
    const result: ExtractionDocument = {
      pages: [
        { pageNumber: 1, isPlan: true },
        { pageNumber: 2, isPlan: false, skippedReason: 'schedule' }
      ]
    };

    expect(skippedPages(result).map((p) => p.pageNumber)).toEqual([2]);
  });
});

describe('calibrationFromExtraction', () => {
  it('derives points per millimetre from the reported scale', () => {
    const calibration = calibrationFromExtraction(planPage([WALL], 1, 100));

    // At 1:100, one paper millimetre covers 100 real mm, so 0.0283 pt per mm.
    expect(calibration!.pointsPerMillimetre).toBeCloseTo(72 / 25.4 / 100, 6);
  });

  it('scales with the drawing', () => {
    const fifty = calibrationFromExtraction(planPage([WALL], 1, 50))!;
    const hundred = calibrationFromExtraction(planPage([WALL], 1, 100))!;

    expect(fifty.pointsPerMillimetre).toBeCloseTo(hundred.pointsPerMillimetre * 2, 6);
  });

  it('ignores non-plan pages and unusable ratios', () => {
    expect(
      calibrationFromExtraction({
        pages: [
          { pageNumber: 1, isPlan: false, scale: { text: '1:50', ratio: 50 } },
          { pageNumber: 2, isPlan: true, scale: { text: 'unknown', ratio: 0 } }
        ]
      })
    ).toBeNull();
  });

  it('returns null when nothing reports a scale', () => {
    expect(calibrationFromExtraction(null)).toBeNull();
  });
});

describe('layersFromExtraction with an "only" filter', () => {
  const mixed = planPage([WALL, DOOR, ROOM]);

  it('takes just the requested classes', () => {
    const layers = layersFromExtraction(mixed, { only: ['rooms'] });

    expect(layers.map((l) => l.name)).toEqual(['Rooms']);
  });

  it('takes everything when no filter is given', () => {
    expect(layersFromExtraction(mixed)).toHaveLength(3);
  });

  it('returns nothing when the filter matches no class', () => {
    expect(layersFromExtraction(mixed, { only: ['stairs'] })).toEqual([]);
  });

  it('carries a room name onto the shape so it can be labelled', () => {
    const layers = layersFromExtraction(mixed, { only: ['rooms'] });

    expect(layers[0]!.shapes[0]!.name).toBe('BEDROOM');
  });
});
