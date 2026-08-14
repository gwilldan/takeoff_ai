import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';

GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString();

export async function loadPdfDocument(file: File): Promise<PDFDocumentProxy> {
  const data = await file.arrayBuffer();
  return getDocument({ data }).promise;
}
