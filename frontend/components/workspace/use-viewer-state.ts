'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ToolId } from '../../lib/annotations/types';

export const ZOOM_LEVELS = [0.25, 0.35, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, 6, 8] as const;
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 8;

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

export type ViewerState = {
  tool: ToolId;
  setTool: (tool: ToolId) => void;
  zoom: number;
  setZoom: (zoom: number) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  currentPage: number;
  goToPage: (pageNumber: number) => void;
};

/**
 * Viewer chrome state: which tool is armed, how far in we are zoomed, and
 * which page is showing. Deliberately separate from annotation state — none of
 * it is persisted, and none of it changes the drawing.
 */
export function useViewerState(pageCount: number): ViewerState {
  const [tool, setTool] = useState<ToolId>('select');
  const [zoom, setZoomRaw] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);

  // Opening a different document must not leave us on a page that is gone.
  useEffect(() => {
    setCurrentPage(1);
  }, [pageCount]);

  const setZoom = useCallback((next: number) => {
    setZoomRaw(clampZoom(next));
  }, []);

  const zoomIn = useCallback(() => {
    setZoomRaw((current) => {
      const next = ZOOM_LEVELS.find((level) => level > current + 0.001);
      return next ?? MAX_ZOOM;
    });
  }, []);

  const zoomOut = useCallback(() => {
    setZoomRaw((current) => {
      const lower = ZOOM_LEVELS.filter((level) => level < current - 0.001);
      return lower[lower.length - 1] ?? MIN_ZOOM;
    });
  }, []);

  const goToPage = useCallback(
    (pageNumber: number) => {
      if (pageCount === 0) {
        return;
      }
      setCurrentPage(Math.min(pageCount, Math.max(1, pageNumber)));
    },
    [pageCount]
  );

  return { tool, setTool, zoom, setZoom, zoomIn, zoomOut, currentPage, goToPage };
}
