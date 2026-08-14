import { polygonArea, polylineLength } from './geometry';
import type { Calibration, Layer, Shape } from './types';

export type LengthUnit = 'mm' | 'm';

const MM_PER_INCH = 25.4;
const POINTS_PER_INCH = 72;

/** Millimetres of paper covered by one PDF point. */
const MM_OF_PAPER_PER_POINT = MM_PER_INCH / POINTS_PER_INCH;

/**
 * Derive a document calibration from a line drawn over a known dimension.
 * Returns null when either input is non-positive, so the caller can keep the
 * dialog open instead of storing an Infinity.
 */
export function calibrationFromLine(
  lengthPts: number,
  realLength: number,
  unit: LengthUnit
): Calibration | null {
  const realMillimetres = unit === 'm' ? realLength * 1000 : realLength;

  if (lengthPts <= 0 || realMillimetres <= 0) {
    return null;
  }

  return { pointsPerMillimetre: lengthPts / realMillimetres };
}

export function formatLength(lengthPts: number, calibration: Calibration | null): string {
  if (!calibration) {
    return `${lengthPts.toFixed(1)} pt`;
  }

  const millimetres = lengthPts / calibration.pointsPerMillimetre;

  // Switch units on the *rendered* value, not the raw one. Calibration
  // arithmetic carries floating-point error — 0.1 × 0.1 is 0.010000000000000002
  // — so a quantity that is mathematically exactly at the boundary can compute
  // a hair below it and print in the smaller unit as "1000 mm".
  if (Math.round(millimetres) < 1000) {
    return `${Math.round(millimetres)} mm`;
  }

  return `${(millimetres / 1000).toFixed(2)} m`;
}

export function formatArea(areaPts2: number, calibration: Calibration | null): string {
  if (!calibration) {
    return `${areaPts2.toFixed(1)} pt²`;
  }

  const squareMillimetres =
    areaPts2 / (calibration.pointsPerMillimetre * calibration.pointsPerMillimetre);
  const squareMetres = squareMillimetres / 1_000_000;

  // As in formatLength: decide on the rendered value so the unit and the number
  // always agree. Comparing the raw squareMetres would print 10 000 mm² for an
  // area that is exactly 0.01 m².
  if (Number(squareMetres.toFixed(2)) >= 0.01) {
    return `${squareMetres.toFixed(2)} m²`;
  }

  return `${Math.round(squareMillimetres)} mm²`;
}

export function shapeMeasurement(shape: Shape, calibration: Calibration | null): string {
  if (shape.kind === 'line') {
    return formatLength(polylineLength(shape.points), calibration);
  }

  return formatArea(polygonArea(shape.points), calibration);
}

/** Total across every shape in a layer, in the layer's own unit. */
export function layerTotal(layer: Layer, calibration: Calibration | null): string {
  if (layer.kind === 'line') {
    const total = layer.shapes.reduce((sum, shape) => sum + polylineLength(shape.points), 0);
    return formatLength(total, calibration);
  }

  const total = layer.shapes.reduce((sum, shape) => sum + polygonArea(shape.points), 0);
  return formatArea(total, calibration);
}

/**
 * The drawing scale as a title-block ratio: millimetres of real world per
 * millimetre of paper.
 */
export function scaleLabel(calibration: Calibration | null): string {
  if (!calibration) {
    return 'Uncalibrated';
  }

  const ratio = MM_OF_PAPER_PER_POINT / calibration.pointsPerMillimetre;

  return `1:${Math.round(ratio)}`;
}
