'use client';

import { useEffect, useState, useRef } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';

type PdfPageProps = {
  pdfDocument: PDFDocumentProxy;
  pageNumber: number;
  contentWidth: number;
};

export function PdfPage({ pdfDocument, pageNumber, contentWidth }: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isRendering, setIsRendering] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null;

    const renderPage = async () => {
      setIsRendering(true);

      const page = await pdfDocument.getPage(pageNumber);
      const canvas = canvasRef.current;

      if (!canvas || cancelled) {
        return;
      }

      const context = canvas.getContext('2d');
      if (!context) {
        return;
      }

      const pageViewport = page.getViewport({ scale: 1 });
      const viewportWidth = contentWidth > 0 ? contentWidth : pageViewport.width;
      const scale = viewportWidth / pageViewport.width;
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

      if (!cancelled) {
        setIsRendering(false);
      }
    };

    renderPage().catch(() => {
      if (!cancelled) {
        setIsRendering(false);
      }
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [contentWidth, pdfDocument, pageNumber]);

  return (
    <figure className="page-shell">
      <figcaption className="page-header">
        <span>Page {pageNumber}</span>
        <span>{isRendering ? 'Rendering' : 'Ready'}</span>
      </figcaption>
      <canvas ref={canvasRef} className="page-canvas" />
    </figure>
  );
}
