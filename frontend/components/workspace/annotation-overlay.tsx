'use client';

import { useCallback, useEffect, useMemo, useState, type PointerEvent } from 'react';
import { hitTestShape, polygonArea, polylineLength } from '../../lib/annotations/geometry';
import { formatArea, formatLength } from '../../lib/annotations/measure';
import { edgeAt, snapPoint, vertexAt } from '../../lib/annotations/snapping';
import { useAnnotations } from '../../lib/annotations/store';
import {
  HIT_TOLERANCE_PX,
  SNAP_TOLERANCE_PX,
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

/**
 * An edit in progress on the selected shape. A vertex drag moves one point and
 * snaps it; a shape drag translates every point. Preview points live here and
 * only reach the store on pointer up, so a drag is one undoable change.
 */
type Edit =
  | { mode: 'vertex'; shape: Shape; index: number; points: Point[]; snapped: { x: boolean; y: boolean } }
  | { mode: 'move'; shape: Shape; origin: Point; points: Point[] };

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
  const [edit, setEdit] = useState<Edit | null>(null);
  const spaceHeldRef = useSpaceHeld();
  const pageGuides = state.guides?.[pageNumber];

  // A draft belongs to one tool on one page. Changing either abandons it.
  useEffect(() => {
    setDraft([]);
    setCursor(null);
    setEdit(null);
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
        const tolerance = HIT_TOLERANCE_PX / scale;
        const selected = visibleShapes.find((entry) => entry.shape.id === state.selectedShapeId)?.shape;

        // A handle on the selected shape takes priority over everything under it.
        if (selected) {
          const index = vertexAt(selected, point, tolerance * 1.6);
          if (index >= 0) {
            event.currentTarget.setPointerCapture(event.pointerId);
            setEdit({ mode: 'vertex', shape: selected, index, points: selected.points, snapped: { x: false, y: false } });
            return;
          }
        }

        // Most recently drawn shape wins when shapes overlap.
        const hit = [...visibleShapes]
          .reverse()
          .find((entry) => hitTestShape(entry.shape, point, tolerance));

        dispatch({ type: 'selectShape', shapeId: hit?.shape.id ?? null });

        if (hit) {
          event.currentTarget.setPointerCapture(event.pointerId);
          setEdit({ mode: 'move', shape: hit.shape, origin: point, points: hit.shape.points });
        }
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
      state.selectedShapeId,
      tool,
      toPointSpace,
      visibleShapes
    ]
  );

  const handlePointerMove = useCallback(
    (event: PointerEvent<SVGSVGElement>) => {
      const point = toPointSpace(event);

      if (!edit) {
        if (isDrawingTool(tool)) {
          setCursor(point);
        }
        return;
      }

      if (edit.mode === 'move') {
        const dx = point.x - edit.origin.x;
        const dy = point.y - edit.origin.y;
        setEdit({ ...edit, points: edit.shape.points.map((p) => ({ x: p.x + dx, y: p.y + dy })) });
        return;
      }

      // Alt disables snapping for the odd case where the drawing is wrong.
      const snap = event.altKey
        ? { point, snappedX: false, snappedY: false }
        : snapPoint(point, SNAP_TOLERANCE_PX / scale, {
            guides: pageGuides,
            shapes: visibleShapes.map((entry) => entry.shape),
            editing: { shape: edit.shape, vertexIndex: edit.index }
          });

      const points = edit.shape.points.map((p, i) => (i === edit.index ? snap.point : p));
      setEdit({ ...edit, points, snapped: { x: snap.snappedX, y: snap.snappedY } });
    },
    [edit, pageGuides, scale, tool, toPointSpace, visibleShapes]
  );

  const handlePointerUp = useCallback(
    (event: PointerEvent<SVGSVGElement>) => {
      if (!edit) {
        return;
      }

      event.currentTarget.releasePointerCapture(event.pointerId);

      const moved = edit.points.some(
        (p, i) => p.x !== edit.shape.points[i]!.x || p.y !== edit.shape.points[i]!.y
      );
      if (moved) {
        dispatch({ type: 'updateShapePoints', shapeId: edit.shape.id, points: edit.points });
      }

      setEdit(null);
    },
    [dispatch, edit]
  );

  // Double-click on the selected shape: a vertex is removed, an edge gains one.
  const handleDoubleClick = useCallback(
    (event: React.MouseEvent<SVGSVGElement>) => {
      if (draft.length > 0) {
        finishDraft();
        return;
      }

      if (tool !== 'select') {
        return;
      }

      const selected = visibleShapes.find((entry) => entry.shape.id === state.selectedShapeId)?.shape;
      if (!selected) {
        return;
      }

      const rect = event.currentTarget.getBoundingClientRect();
      const point = { x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale };
      const tolerance = HIT_TOLERANCE_PX / scale;

      const vertex = vertexAt(selected, point, tolerance * 1.6);
      const minimum = selected.kind === 'area' ? 3 : 2;
      if (vertex >= 0 && selected.points.length > minimum) {
        dispatch({
          type: 'updateShapePoints',
          shapeId: selected.id,
          points: selected.points.filter((_, i) => i !== vertex)
        });
        return;
      }

      const edge = edgeAt(selected, point, tolerance);
      if (edge >= 0) {
        const points = [...selected.points];
        points.splice(edge + 1, 0, point);
        dispatch({ type: 'updateShapePoints', shapeId: selected.id, points });
      }
    },
    [dispatch, draft.length, finishDraft, scale, state.selectedShapeId, tool, visibleShapes]
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
      onPointerMove={interactive ? handlePointerMove : undefined}
      onPointerUp={interactive ? handlePointerUp : undefined}
      onPointerLeave={() => setCursor(null)}
      onDoubleClick={interactive ? handleDoubleClick : undefined}
      className={`absolute left-0 top-0 ${interactive ? cursorClass : 'pointer-events-none'}`}
      role="presentation"
    >
      {visibleShapes.map(({ shape: stored, color }) => {
        const selected = stored.id === state.selectedShapeId;
        const shape = edit && edit.shape.id === stored.id ? { ...stored, points: edit.points } : stored;
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
              ? shape.points.map((point, index) => {
                  const dragging = edit?.mode === 'vertex' && edit.index === index;
                  return (
                    <circle
                      key={index}
                      cx={point.x}
                      cy={point.y}
                      r={(dragging ? 5 : 3.5) / scale}
                      fill={dragging && (edit.snapped.x || edit.snapped.y) ? color : '#ffffff'}
                      stroke={color}
                      strokeWidth={1.5}
                      vectorEffect="non-scaling-stroke"
                      className="cursor-move"
                    />
                  );
                })
              : null}

            {/* Snap guides: a full-height or full-width hairline on the axis the
                dragged vertex locked to, so it is obvious what it aligned with. */}
            {selected && edit?.mode === 'vertex' && edit.shape.id === stored.id ? (
              <g stroke={color} strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" opacity={0.7}>
                {edit.snapped.x ? (
                  <line x1={edit.points[edit.index]!.x} y1={0} x2={edit.points[edit.index]!.x} y2={pageSize.height} />
                ) : null}
                {edit.snapped.y ? (
                  <line x1={0} y1={edit.points[edit.index]!.y} x2={pageSize.width} y2={edit.points[edit.index]!.y} />
                ) : null}
              </g>
            ) : null}

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
