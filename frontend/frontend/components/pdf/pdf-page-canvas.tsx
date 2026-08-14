'use client';

import { useEffect, useRef } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';

export type PdfPageCanvasProps = {
  pdfDocument: PDFDocumentProxy;
  pageNumber: number;
  /** CSS pixels per PDF point. */
  scale: number;
  className?: string;
};

/**
 * Renders one page at the given scale. The canvas backing store is multiplied
 * by devicePixelRatio for sharpness while the CSS box stays in the caller's
 * coordinate space, so an overlay positioned over this element lines up with
 * PDF point coordinates scaled by `scale`.
 */
export function PdfPageCanvas({ pdfDocument, pageNumber, scale, className }: PdfPageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null;

    const render = async () => {
      const page = await pdfDocument.getPage(pageNumber);
      const canvas = canvasRef.current;

      if (!canvas || cancelled) {
        return;
      }

      const context = canvas.getContext('2d');

      if (!context) {
        return;
      }

      const viewport = page.getViewport({ scale });
      const outputScale = window.devicePixelRatio || 1;

      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;

      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, canvas.width, canvas.height);

      renderTask = page.render({
        canvasContext: context,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined
      });

      await renderTask.promise;
    };

    render().catch(() => {
      // A cancelled render throws; there is nothing to recover.
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdfDocument, pageNumber, scale]);

  return <canvas ref={canvasRef} className={className} />;
}
