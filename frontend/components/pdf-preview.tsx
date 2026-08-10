'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import { loadPdfDocument } from '../lib/pdfjs';
import { PdfPage } from './pdf-page';

type PdfPreviewProps = {
  file: File | null;
};

export function PdfPreview({ file }: PdfPreviewProps) {
  const previewRef = useRef<HTMLDivElement | null>(null);
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [contentWidth, setContentWidth] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const element = previewRef.current;
    if (!element) {
      return;
    }

    const updateWidth = () => {
      setContentWidth(element.clientWidth);
    };

    updateWidth();

    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let activeDocument: PDFDocumentProxy | null = null;

    if (!file) {
      setPdfDocument(null);
      setPageCount(0);
      setLoading(false);
      setError('');
      return;
    }

    setLoading(true);
    setError('');
    setPdfDocument(null);
    setPageCount(0);

    loadPdfDocument(file)
      .then((document) => {
        if (cancelled) {
          document.destroy();
          return;
        }

        activeDocument = document;
        setPdfDocument(document);
        setPageCount(document.numPages);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load PDF preview.');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (activeDocument) {
        void activeDocument.destroy();
      }
    };
  }, [file]);

  const pageNumbers = useMemo(() => {
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  }, [pageCount]);

  return (
    <section className="card preview-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Live preview</p>
          <h2>Rendered with PDF.js</h2>
        </div>
        <span className="pill">{pageCount ? `${pageCount} pages` : 'No document yet'}</span>
      </div>

      <div ref={previewRef} className="preview-scroll">
        {!file ? (
          <div className="preview-empty">
            <strong>No PDF selected</strong>
            <p>Choose a document to render every page locally before sending it to the backend.</p>
          </div>
        ) : null}

        {loading ? <div className="preview-empty">Loading document...</div> : null}

        {error ? <div className="preview-empty preview-error">{error}</div> : null}

        {pdfDocument ? (
          <div className="page-list">
            {pageNumbers.map((pageNumber) => (
              <PdfPage
                key={pageNumber}
                pdfDocument={pdfDocument}
                pageNumber={pageNumber}
                contentWidth={Math.max(contentWidth - 2, 320)}
              />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
