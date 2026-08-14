'use client';

import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import { PdfPageCanvas } from '../pdf/pdf-page-canvas';
import type { PdfPageSize } from '../pdf/use-pdf-document';

export const THUMBNAIL_WIDTH_PX = 140;

export type PageThumbnailProps = {
  pdfDocument: PDFDocumentProxy;
  pageNumber: number;
  pageSize: PdfPageSize;
  active: boolean;
  annotated: boolean;
  onSelect: (pageNumber: number) => void;
};

export function PageThumbnail({
  pdfDocument,
  pageNumber,
  pageSize,
  active,
  annotated,
  onSelect
}: PageThumbnailProps) {
  const wrapperRef = useRef<HTMLButtonElement | null>(null);
  const [visible, setVisible] = useState(false);

  // Render only what the user can nearly see. Mounting every canvas at once
  // queues one pdf.js render task per page and blocks interaction.
  useEffect(() => {
    const element = wrapperRef.current;

    if (!element || visible) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '300px 0px' }
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, [visible]);

  const scale = THUMBNAIL_WIDTH_PX / pageSize.width;
  const height = Math.round(pageSize.height * scale);

  return (
    <button
      ref={wrapperRef}
      type="button"
      onClick={() => onSelect(pageNumber)}
      aria-current={active ? 'page' : undefined}
      className="block w-full text-left"
    >
      <div
        style={{ height }}
        className={`relative overflow-hidden rounded-[3px] border bg-white transition-colors ${
          active ? 'border-accent ring-1 ring-accent/40' : 'border-edge hover:border-edge-strong'
        }`}
      >
        {visible ? (
          <PdfPageCanvas
            pdfDocument={pdfDocument}
            pageNumber={pageNumber}
            scale={scale}
            className="block"
          />
        ) : null}
        {annotated ? (
          <span aria-hidden className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-accent" />
        ) : null}
      </div>
      <span
        className={`mt-1 block text-center font-mono text-[10px] ${
          active ? 'text-accent' : 'text-ink-faint'
        }`}
      >
        {pageNumber}
        {annotated ? <span className="sr-only"> (annotated)</span> : null}
      </span>
    </button>
  );
}
