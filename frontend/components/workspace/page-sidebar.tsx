'use client';

import { useMemo, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import type { PdfPageSize } from '../pdf/use-pdf-document';

export type PageSidebarProps = {
  pdfDocument: PDFDocumentProxy | null;
  pageSizes: PdfPageSize[];
  currentPage: number;
  onPageChange: (pageNumber: number) => void;
};

export function PageSidebar({
  pdfDocument,
  pageSizes,
  currentPage,
  onPageChange
}: PageSidebarProps) {
  const state = {
    layers: [] as any
  }
  const [onlyAnnotated, setOnlyAnnotated] = useState(false);

  const annotatedPages = useMemo(() => {
    const pages = new Set<number>();

    for (const layer of state.layers) {
      for (const shape of layer.shapes) {
        pages.add(shape.pageNumber);
      }
    }

    return pages;
  }, [state.layers]);

  const pageNumbers = useMemo(() => {
    const all = pageSizes.map((_, index) => index + 1);
    return onlyAnnotated ? all.filter((pageNumber) => annotatedPages.has(pageNumber)) : all;
  }, [annotatedPages, onlyAnnotated, pageSizes]);

  const filterAvailable = annotatedPages.size > 0;

  return (
    <aside className="flex min-h-0 flex-col border-l border-edge bg-chrome-2">
      <div className="shrink-0 border-b border-edge px-2.5 py-2">
        <p className="pane-label">Sheets</p>
        <label
          className={`mt-2 flex items-center gap-2 text-[11px] ${
            filterAvailable ? 'text-ink-soft' : 'cursor-not-allowed text-ink-faint'
          }`}
        >
          <input
            type="checkbox"
            checked={onlyAnnotated}
            disabled={!filterAvailable}
            onChange={(event) => setOnlyAnnotated(event.target.checked)}
            className="h-3 w-3 accent-[var(--color-accent)]"
          />
          Only annotated
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        {!pdfDocument ? (
          <p className="font-mono text-[10px] leading-relaxed text-ink-faint">
            Open a document to see its sheets.
          </p>
        ) : (
          <div className="space-y-2">
            {pageNumbers.map((pageNumber) => (
             <div>{pageNumber}</div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
