'use client';

import { useEffect, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import { loadPdfDocument } from '../../lib/pdfjs';

export type PdfPageSize = { width: number; height: number };

export type PdfDocumentState = {
  document: PDFDocumentProxy | null;
  pageCount: number;
  pageSizes: PdfPageSize[];
  loading: boolean;
  error: string;
};

const EMPTY: PdfDocumentState = {
  document: null,
  pageCount: 0,
  pageSizes: [],
  loading: false,
  error: ''
};

/**
 * Loads a File into a pdf.js document and reads every page's size in PDF
 * points at scale 1. Sizes are read once up front so consumers can lay out
 * pages and annotation overlays without awaiting per-page promises.
 */
export function usePdfDocument(file: File | null): PdfDocumentState {
  const [state, setState] = useState<PdfDocumentState>(EMPTY);

  useEffect(() => {
    if (!file) {
      setState(EMPTY);
      return;
    }

    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;

    setState({ ...EMPTY, loading: true });

    const load = async () => {
      const document = await loadPdfDocument(file);

      if (cancelled) {
        document.destroy();
        return;
      }

      loaded = document;

      const pageSizes: PdfPageSize[] = [];

      for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
        const page = await document.getPage(pageNumber);
        const viewport = page.getViewport({ scale: 1 });
        pageSizes.push({ width: viewport.width, height: viewport.height });
      }

      if (cancelled) {
        return;
      }

      setState({
        document,
        pageCount: document.numPages,
        pageSizes,
        loading: false,
        error: ''
      });
    };

    load().catch((error: unknown) => {
      if (cancelled) {
        return;
      }

      setState({
        ...EMPTY,
        error: error instanceof Error ? error.message : 'Failed to load this PDF.'
      });
    });

    return () => {
      cancelled = true;
      if (loaded) {
        loaded.destroy();
      }
    };
  }, [file]);

  return state;
}
