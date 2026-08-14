'use client';

import { useCallback, useEffect, useMemo, useState, type PointerEvent } from 'react';
import { hitTestShape, polygonArea, polylineLength } from '../../lib/annotations/geometry';
import { formatArea, formatLength } from '../../lib/annotations/measure';
import { useAnnotations } from '../../lib/annotations/store';
import {
  HIT_TOLERANCE_PX,
  type Point,
  type Shape,
  type ShapeKind,
  type ToolId
} from '../../lib/annotations/types';
import type { PdfPageSize } from '../pdf/use-pdf-document';
import { useSpaceHeld } from './use-space-held';

/** How near the first vertex a click must land to close an area, in screen px. */
const CLOSE_TOLERANCE_PX = 8;

/** Consecutive vertices nearer than this (in PDF points) are treated as one. */
const DEDUPE_TOLERANCE_PTS = 0.5;

export type AnnotationOverlayProps = {
  pageNumber: number;
  pageSize: PdfPageSize;
  /** CSS pixels per PDF point. */
  scale: number;
  tool: ToolId;
  onCalibrationLine: (lengthPts: number) => void;
};

function isDrawingTool(tool: ToolId): tool is 'line' | 'area' | 'calibrate' {
  return tool === 'line' || tool === 'area' || tool === 'calibrate';
}

function dedupe(points: Point[]): Point[] {
  return points.filter((point, index) => {
    if (index === 0) {
      return true;
    }

    const previous = points[index - 1]!;
    return Math.hypot(point.x - previous.x, point.y - previous.y) > DEDUPE_TOLERANCE_PTS;
  });
}

export function AnnotationOverlay({
  pageNumber,
  pageSize,
  scale,
  tool,
  onCalibrationLine
}: AnnotationOverlayProps) {
  const { state, dispatch } = useAnnotations();
  const [draft, setDraft] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const spaceHeldRef = useSpaceHeld();

  // A draft belongs to one tool on one page. Changing either abandons it.
  useEffect(() => {
    setDraft([]);
    setCursor(null);
  }, [tool, pageNumber]);

  const visibleShapes = useMemo(() => {
    const entries: Array<{ shape: Shape; color: string }> = [];

    for (const layer of state.layers) {
      if (!layer.visible) {
        continue;
      }

      for (const shape of layer.shapes) {
        if (shape.pageNumber === pageNumber) {
          entries.push({ shape, color: layer.color });
        }
      }
    }

    return entries;
  }, [pageNumber, state.layers]);

  const commitShape = useCallback(
    (kind: ShapeKind, points: Point[]) => {
      const cleaned = dedupe(points);
      const minimum = kind === 'area' ? 3 : 2;

      if (cleaned.length < minimum) {
        return;
      }

      dispatch({
        type: 'addShape',
        shape: { id: crypto.randomUUID(), kind, pageNumber, points: cleaned },
        fallbackLayerId: crypto.randomUUID()
      });
    },
    [dispatch, pageNumber]
  );

  const finishDraft = useCallback(() => {
    if (tool === 'line' || tool === 'area') {
      commitShape(tool, draft);
    }

    setDraft([]);
    setCursor(null);
  }, [commitShape, draft, tool]);

  // Keyboard: Enter commits, Escape abandons, Delete removes the selection.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;

      // Never hijack keys while a text field has focus.
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }

      if (event.key === 'Escape') {
        setDraft([]);
        setCursor(null);
        return;
      }

      if (event.key === 'Enter' && draft.length > 0) {
        event.preventDefault();
        finishDraft();
        return;
      }

      if ((event.key === 'Delete' || event.key === 'Backspace') && state.selectedShapeId) {
        event.preventDefault();
        dispatch({ type: 'deleteShape', shapeId: state.selectedShapeId });
      }
    };

    window.addEventListener('keydown', onKeyDown);

    return () => window.removeEventListener('keydown', onKeyDown);
  }, [dispatch, draft.length, finishDraft, state.selectedShapeId]);

  const toPointSpace = useCallback(
    (event: PointerEvent<SVGSVGElement>): Point => {
      const rect = event.currentTarget.getBoundingClientRect();

      return {
        x: (event.clientX - rect.left) / scale,
        y: (event.clientY - rect.top) / scale
      };
    },
    [scale]
  );

  const handlePointerDown = useCallback(
    (event: PointerEvent<SVGSVGElement>) => {
      // Left button only, and never while a space-drag pan is starting — this
      // handler runs before the scroll container's, so without the space check
      // panning with a drawing tool armed would also drop a vertex.
      if (event.button !== 0 || spaceHeldRef.current) {
        return;
      }

      const point = toPointSpace(event);

      if (tool === 'select') {
        // Most recently drawn shape wins when shapes overlap.
        const hit = [...visibleShapes]
          .reverse()
          .find((entry) => hitTestShape(entry.shape, point, HIT_TOLERANCE_PX / scale));

        dispatch({ type: 'selectShape', shapeId: hit?.shape.id ?? null });
        return;
      }

      if (!isDrawingTool(tool)) {
        return;
      }

      if (tool === 'calibrate') {
        if (draft.length === 0) {
          setDraft([point]);
          return;
        }

        const length = polylineLength([draft[0]!, point]);
        setDraft([]);
        setCursor(null);

        if (length > 0) {
          onCalibrationLine(length);
        }

        return;
      }

      // Closing an area by clicking its first vertex.
      const first = draft[0];

      if (
        tool === 'area' &&
        first &&
        draft.length >= 3 &&
        Math.hypot(point.x - first.x, point.y - first.y) <= CLOSE_TOLERANCE_PX / scale
      ) {
        commitShape('area', draft);
        setDraft([]);
        setCursor(null);
        return;
      }

      setDraft((current) => [...current, point]);
    },
    [
      commitShape,
      dispatch,
      draft,
      onCalibrationLine,
      scale,
      spaceHeldRef,
      tool,
      toPointSpace,
      visibleShapes
    ]
  );

  const draftPreview = useMemo(() => {
    if (draft.length === 0) {
      return null;
    }

    const points = cursor ? [...draft, cursor] : draft;
    const label =
      tool === 'area'
        ? formatArea(polygonArea(points), state.calibration)
        : formatLength(polylineLength(points), state.calibration);

    return { points, label, anchor: points[points.length - 1]! };
  }, [cursor, draft, state.calibration, tool]);

  const interactive = tool !== 'pan';
  const cursorClass = isDrawingTool(tool) ? 'cursor-crosshair' : 'cursor-default';

  return (
    <svg
      width={pageSize.width * scale}
      height={pageSize.height * scale}
      viewBox={`0 0 ${pageSize.width} ${pageSize.height}`}
      onPointerDown={interactive ? handlePointerDown : undefined}
      onPointerMove={isDrawingTool(tool) ? (event) => setCursor(toPointSpace(event)) : undefined}
      onPointerLeave={() => setCursor(null)}
      onDoubleClick={draft.length > 0 ? finishDraft : undefined}
      className={`absolute left-0 top-0 ${interactive ? cursorClass : 'pointer-events-none'}`}
      role="presentation"
    >
      {visibleShapes.map(({ shape, color }) => {
        const selected = shape.id === state.selectedShapeId;
        const pointsAttribute = shape.points.map((point) => `${point.x},${point.y}`).join(' ');

        return (
          <g key={shape.id}>
            {shape.kind === 'area' ? (
              <polygon
                points={pointsAttribute}
                fill={color}
                fillOpacity={0.22}
                stroke={color}
                strokeWidth={selected ? 3 : 1.75}
                vectorEffect="non-scaling-stroke"
              />
            ) : (
              <polyline
                points={pointsAttribute}
                fill="none"
                stroke={color}
                strokeWidth={selected ? 3.5 : 2}
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            )}

            {selected
              ? shape.points.map((point, index) => (
                  <circle
                    key={index}
                    cx={point.x}
                    cy={point.y}
                    r={3.5 / scale}
                    fill="#ffffff"
                    stroke={color}
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                ))
              : null}

            {/* A named element carries its label always — a room outline nobody
                can identify is not an annotation. References and grid marks show
                on selection, where they answer "which one is this on the paper?"
                without crowding every shape on the sheet. */}
            {shape.name ? (
              <text
                x={shape.points.reduce((sum, p) => sum + p.x, 0) / shape.points.length}
                y={shape.points.reduce((sum, p) => sum + p.y, 0) / shape.points.length}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize={9 / scale}
                fill={color}
                stroke="#ffffff"
                strokeWidth={3 / scale}
                paintOrder="stroke"
              >
                {shape.name}
              </text>
            ) : null}

            {selected && (shape.reference || shape.gridRef) ? (
              <text
                x={shape.points[0]!.x + 8 / scale}
                y={shape.points[0]!.y - 8 / scale}
                fontFamily="var(--font-mono)"
                fontSize={10 / scale}
                fill={color}
                stroke="#ffffff"
                strokeWidth={3 / scale}
                paintOrder="stroke"
              >
                {[shape.reference, shape.gridRef].filter(Boolean).join('  ')}
              </text>
            ) : null}
          </g>
        );
      })}

      {draftPreview ? (
        <g>
          <polyline
            points={draftPreview.points.map((point) => `${point.x},${point.y}`).join(' ')}
            fill={tool === 'area' ? 'var(--color-accent)' : 'none'}
            fillOpacity={tool === 'area' ? 0.14 : undefined}
            stroke="var(--color-accent)"
            strokeWidth={2}
            strokeDasharray="6 4"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />

          {draft.map((point, index) => (
            <rect
              key={index}
              x={point.x - 2.5 / scale}
              y={point.y - 2.5 / scale}
              width={5 / scale}
              height={5 / scale}
              fill="#ffffff"
              stroke="var(--color-accent)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          <text
            x={draftPreview.anchor.x + 8 / scale}
            y={draftPreview.anchor.y - 8 / scale}
            fontFamily="var(--font-mono)"
            fontSize={11 / scale}
            fill="var(--color-ink)"
            stroke="#ffffff"
            strokeWidth={3 / scale}
            paintOrder="stroke"
          >
            {draftPreview.label}
          </text>
        </g>
      ) : null}
    </svg>
  );
}
