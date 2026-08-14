'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { usePdfDocument } from '../pdf/use-pdf-document';
import { useAnnotations } from '../../lib/annotations/store';
import { scaleLabel } from '../../lib/annotations/measure';
import {
  calibrationFromExtraction,
  layersFromExtraction,
  type ExtractionDocument
} from '../../lib/annotations/extraction-import';
import { documentKeyFor, loadAnnotations } from '../../lib/annotations/storage';
import { AnnotationOverlay } from './annotation-overlay';
import { EmptyState } from './empty-state';
import { ExtractionPanel } from './extraction-panel';
import { LayerSidebar } from './layer-sidebar';
import { PageSidebar } from './page-sidebar';
import { PdfViewer } from './pdf-viewer';
import { ScaleDialog } from './scale-dialog';
import { TopBar } from './top-bar';
import { useExtractionJob } from './use-extraction-job';
import { useViewerState, clampZoom } from './use-viewer-state';
import { ViewerToolbar } from './viewer-toolbar';

/**
 * Component classes taken from an extraction today.
 *
 * Rooms only: they are anchored on the drawing's printed area tags, which every
 * BIM-produced plan carries, so they are right across drawing styles. Wall
 * reconstruction still depends on a convention not all drawings follow, and
 * annotating a drawing with walls that are not there is worse than annotating
 * nothing.
 */
const IMPORTED_LAYERS = ['rooms'];

export function Workspace() {
  const [file, setFile] = useState<File | null>(null);
  const [availableWidth, setAvailableWidth] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const [notificationUrl, setNotificationUrl] = useState('');
  const [pendingCalibrationPts, setPendingCalibrationPts] = useState<number | null>(null);
  const [importedAt, setImportedAt] = useState<string | null>(null);
  const autoImportedRef = useRef<string | null>(null);
  const extraction = useExtractionJob();
  const { state, dispatch } = useAnnotations();
  const { document: pdfDocument, pageCount, pageSizes, loading, error } = usePdfDocument(file);
  const viewer = useViewerState(pageCount);

  const pageSize = pageSizes[viewer.currentPage - 1] ?? null;
  const scaleText = scaleLabel(state.calibration);

  const documentKey = file && pageCount > 0 ? documentKeyFor(file, pageCount) : null;

  // Adopt any annotations saved against this exact document. This must run in an
  // effect, not during render — dispatching to a provider mid-render throws
  // "Cannot update a component while rendering a different component".
  useEffect(() => {
    if (!documentKey || state.documentKey === documentKey) {
      return;
    }

    dispatch({ type: 'loadDocument', documentKey, restored: loadAnnotations(documentKey) });
  }, [documentKey, state.documentKey, dispatch]);

  const handleFitWidth = useCallback(() => {
    if (!pageSize || availableWidth === 0) {
      return;
    }

    // 48px accounts for the viewer's p-6 padding on both sides.
    viewer.setZoom(clampZoom((availableWidth - 48) / pageSize.width));
  }, [availableWidth, pageSize, viewer]);

  const handleZoomBy = useCallback(
    (factor: number) => {
      viewer.setZoom(clampZoom(viewer.zoom * factor));
    },
    [viewer]
  );

  const handleImportExtraction = useCallback(
    (result: ExtractionDocument) => {
      dispatch({
        type: 'importExtraction',
        layers: layersFromExtraction(result, { only: IMPORTED_LAYERS }),
        // Adopt the drawing's own scale so totals read in millimetres straight
        // away, unless the user has already calibrated by hand.
        calibration: state.calibration ?? calibrationFromExtraction(result)
      });
      setImportedAt(new Date().toISOString());
    },
    [dispatch, state.calibration]
  );

  // Annotate as soon as the job finishes. Guarded on the completion timestamp so
  // re-renders and continued polling cannot re-import over the user's edits.
  const completedAt = extraction.job?.status === 'completed' ? extraction.job.id : null;

  useEffect(() => {
    if (!completedAt || autoImportedRef.current === completedAt) {
      return;
    }

    const result = extraction.job?.result as ExtractionDocument | undefined;
    if (!result) {
      return;
    }

    autoImportedRef.current = completedAt;
    handleImportExtraction(result);
  }, [completedAt, extraction.job, handleImportExtraction]);

  const handleRunExtraction = useCallback(() => {
    if (!file) {
      return;
    }

    setPanelOpen(true);
    void extraction.start(file, notificationUrl || undefined);
  }, [extraction, file, notificationUrl]);

  return (
    <div className="flex h-screen flex-col">
      <TopBar
        fileName={file?.name ?? null}
        scaleText={scaleText}
        onOpenFile={setFile}
        onRunExtraction={handleRunExtraction}
        extractionBusy={extraction.isRunning}
        extractionLabel={extraction.isRunning ? 'Extracting…' : 'Run extraction'}
        onToggleResults={() => setPanelOpen((open) => !open)}
        resultsBadge={extraction.job ? extraction.job.status : null}
      />

      <div className="relative grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_220px]">
        <LayerSidebar />

        <section className="flex min-h-0 flex-col">
          {pdfDocument && pageSize ? (
            <>
              <ViewerToolbar
                tool={viewer.tool}
                onToolChange={viewer.setTool}
                zoom={viewer.zoom}
                onZoomIn={viewer.zoomIn}
                onZoomOut={viewer.zoomOut}
                onFitWidth={handleFitWidth}
                currentPage={viewer.currentPage}
                pageCount={pageCount}
                onPageChange={viewer.goToPage}
                scaleText={scaleText}
              />
              <PdfViewer
                pdfDocument={pdfDocument}
                pageNumber={viewer.currentPage}
                pageSize={pageSize}
                zoom={viewer.zoom}
                tool={viewer.tool}
                onAvailableWidth={setAvailableWidth}
                onZoomBy={handleZoomBy}
                overlay={
                  <AnnotationOverlay
                    pageNumber={viewer.currentPage}
                    pageSize={pageSize}
                    scale={viewer.zoom}
                    tool={viewer.tool}
                    onCalibrationLine={setPendingCalibrationPts}
                  />
                }
              />
            </>
          ) : (
            <div className="canvas-field flex min-h-0 flex-1 items-center justify-center">
              {loading ? (
                <p className="font-mono text-xs text-ink-soft">Loading document…</p>
              ) : error ? (
                <p className="max-w-sm rounded-lg bg-panel px-4 py-3 text-center text-sm text-accent">
                  {error}
                </p>
              ) : (
                <EmptyState onOpenFile={setFile} />
              )}
            </div>
          )}
        </section>

        <PageSidebar
          pdfDocument={pdfDocument}
          pageSizes={pageSizes}
          currentPage={viewer.currentPage}
          onPageChange={viewer.goToPage}
        />

        <ExtractionPanel
          open={panelOpen}
          onClose={() => setPanelOpen(false)}
          job={extraction.job}
          message={extraction.message}
          isRunning={extraction.isRunning}
          notificationUrl={notificationUrl}
          onNotificationUrlChange={setNotificationUrl}
          onImport={handleImportExtraction}
          importedAt={importedAt}
        />

        {pendingCalibrationPts !== null ? (
          <ScaleDialog
            drawnLengthPts={pendingCalibrationPts}
            onCancel={() => setPendingCalibrationPts(null)}
            onConfirm={(calibration) => {
              dispatch({ type: 'setCalibration', calibration });
              setPendingCalibrationPts(null);
              // Leaving the calibrate tool armed invites overwriting the scale
              // that was just set.
              viewer.setTool('select');
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
