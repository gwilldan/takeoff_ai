'use client';

import {
  useCallback,
  useEffect,
  useRef,
  type PointerEvent,
  type ReactNode,
  type WheelEvent
} from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import { PdfPageCanvas } from '../pdf/pdf-page-canvas';
import type { PdfPageSize } from '../pdf/use-pdf-document';

export type PdfViewerProps = {
  pdfDocument: PDFDocumentProxy;
  pageNumber: number;
  pageSize: PdfPageSize;
  zoom: number;
  onAvailableWidth: (width: number) => void;
  onZoomBy: (factor: number) => void;
  /** Rendered inside the sized page wrapper, above the canvas. */
  overlay?: ReactNode;
};

export function PdfViewer({
  pdfDocument,
  pageNumber,
  pageSize,
  zoom,
  onAvailableWidth,
  onZoomBy,
  overlay
}: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const panRef = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  // Publish the available width so the toolbar's Fit button has something to
  // divide the page width by.
  useEffect(() => {
    const element = scrollRef.current;

    if (!element) {
      return;
    }

    const report = () => onAvailableWidth(element.clientWidth);
    report();

    const observer = new ResizeObserver(report);
    observer.observe(element);

    return () => observer.disconnect();
  }, [onAvailableWidth]);


  const handlePointerMove = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    const element = scrollRef.current;

    if (!pan || !element) {
      return;
    }

    element.scrollLeft = pan.left - (event.clientX - pan.x);
    element.scrollTop = pan.top - (event.clientY - pan.y);
  }, []);

  const handlePointerUp = useCallback((event: PointerEvent<HTMLDivElement>) => {
    panRef.current = null;
    scrollRef.current?.releasePointerCapture(event.pointerId);
  }, []);

  const handleWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }

      event.preventDefault();
      onZoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1);
    },
    [onZoomBy]
  );

  const width = pageSize.width * zoom;
  const height = pageSize.height * zoom;
//   const panning = tool === 'pan';

  return (
    <div
      ref={scrollRef}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onWheel={handleWheel}
      className={`canvas-field relative flex-1 overflow-auto cursor-grab active:cursor-grabbing
      }`}
    >
      <div className="flex min-h-full min-w-full items-start justify-center p-6">
        <div className="relative shadow-[0_2px_16px_rgba(0,0,0,0.18)]" style={{ width, height }}>
          <PdfPageCanvas
            pdfDocument={pdfDocument}
            pageNumber={pageNumber}
            scale={zoom}
            className="block bg-white"
          />
          {overlay}
        </div>
      </div>
    </div>
  );
}
