import { describe, expect, it } from 'vitest';
import {
  calibrationFromLine,
  formatArea,
  formatLength,
  layerTotal,
  scaleLabel,
  shapeMeasurement
} from './measure';
import type { Layer, Shape } from './types';

const TENTHS = { pointsPerMillimetre: 0.1 };

function layer(kind: 'line' | 'area', shapes: Shape[]): Layer {
  return { id: 'l1', name: 'Layer 1', color: '#2563EB', kind, visible: true, shapes };
}

describe('calibrationFromLine', () => {
  it('derives points per millimetre from a millimetre input', () => {
    expect(calibrationFromLine(100, 1000, 'mm')).toEqual({ pointsPerMillimetre: 0.1 });
  });

  it('converts a metre input to millimetres first', () => {
    expect(calibrationFromLine(100, 1, 'm')).toEqual({ pointsPerMillimetre: 0.1 });
  });

  it('rejects a zero-length drawn line', () => {
    expect(calibrationFromLine(0, 1000, 'mm')).toBeNull();
  });

  it('rejects a non-positive real length', () => {
    expect(calibrationFromLine(100, 0, 'mm')).toBeNull();
    expect(calibrationFromLine(100, -5, 'mm')).toBeNull();
  });
});

describe('formatLength', () => {
  it('falls back to points when uncalibrated', () => {
    expect(formatLength(123.45, null)).toBe('123.5 pt');
  });

  it('uses millimetres below one metre', () => {
    expect(formatLength(50, TENTHS)).toBe('500 mm');
  });

  it('uses metres at one metre and above', () => {
    expect(formatLength(100, TENTHS)).toBe('1.00 m');
    expect(formatLength(1234, TENTHS)).toBe('12.34 m');
  });
});

describe('formatArea', () => {
  it('falls back to square points when uncalibrated', () => {
    expect(formatArea(123.45, null)).toBe('123.5 pt²');
  });

  it('uses square metres for anything from 0.01 m² up', () => {
    // 10000 pt² / 0.1² = 1_000_000 mm² = 1 m²
    expect(formatArea(10000, TENTHS)).toBe('1.00 m²');
  });

  it('uses square millimetres below 0.01 m²', () => {
    // 1 pt² / 0.1² = 100 mm²
    expect(formatArea(1, TENTHS)).toBe('100 mm²');
  });
});

describe('shapeMeasurement', () => {
  const square = [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 10, y: 10 },
    { x: 0, y: 10 }
  ];

  it('measures a line shape as length', () => {
    const shape: Shape = { id: 's', kind: 'line', pageNumber: 1, points: square };
    expect(shapeMeasurement(shape, TENTHS)).toBe('300 mm');
  });

  it('measures an area shape as area', () => {
    const shape: Shape = { id: 's', kind: 'area', pageNumber: 1, points: square };
    // 100 pt² / 0.01 = 10_000 mm² = 0.01 m²
    expect(shapeMeasurement(shape, TENTHS)).toBe('0.01 m²');
  });
});

describe('layerTotal', () => {
  it('sums lengths across every shape in a line layer', () => {
    const shapes: Shape[] = [
      { id: 'a', kind: 'line', pageNumber: 1, points: [{ x: 0, y: 0 }, { x: 30, y: 0 }] },
      { id: 'b', kind: 'line', pageNumber: 2, points: [{ x: 0, y: 0 }, { x: 70, y: 0 }] }
    ];
    expect(layerTotal(layer('line', shapes), TENTHS)).toBe('1.00 m');
  });

  it('sums areas across every shape in an area layer', () => {
    const box = (size: number) => [
      { x: 0, y: 0 },
      { x: size, y: 0 },
      { x: size, y: size },
      { x: 0, y: size }
    ];
    const shapes: Shape[] = [
      { id: 'a', kind: 'area', pageNumber: 1, points: box(50) },
      { id: 'b', kind: 'area', pageNumber: 1, points: box(50) }
    ];
    // 2 * 2500 pt² = 5000 pt² → 500_000 mm² → 0.50 m²
    expect(layerTotal(layer('area', shapes), TENTHS)).toBe('0.50 m²');
  });

  it('reports zero for an empty layer', () => {
    expect(layerTotal(layer('line', []), TENTHS)).toBe('0 mm');
    expect(layerTotal(layer('area', []), TENTHS)).toBe('0 mm²');
  });
});

describe('scaleLabel', () => {
  it('reports uncalibrated state', () => {
    expect(scaleLabel(null)).toBe('Uncalibrated');
  });

  it('reports a 1:N ratio', () => {
    // 1 pt = 25.4/72 mm of paper; at 0.1 pt/mm one paper mm covers 3.527 real mm
    expect(scaleLabel(TENTHS)).toBe('1:4');
    // A true 1:100 drawing: 1 mm paper = 100 mm real
    expect(scaleLabel({ pointsPerMillimetre: 25.4 / 72 / 100 })).toBe('1:100');
  });
});
