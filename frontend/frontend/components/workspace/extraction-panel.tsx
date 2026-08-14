'use client';

import { useMemo } from 'react';
import type { JobRecord } from '../../lib/api';
import {
  isRejected,
  layersFromExtraction,
  skippedPages,
  type ExtractionDocument
} from '../../lib/annotations/extraction-import';

export type ExtractionPanelProps = {
  open: boolean;
  onClose: () => void;
  job: JobRecord | null;
  message: string;
  isRunning: boolean;
  notificationUrl: string;
  onNotificationUrlChange: (value: string) => void;
  onImport: (result: ExtractionDocument) => void;
  importedAt: string | null;
};

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-chrome text-ink-soft',
  processing: 'bg-accent-soft text-accent',
  completed: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700'
};

export function ExtractionPanel({
  open,
  onClose,
  job,
  message,
  isRunning,
  notificationUrl,
  onNotificationUrlChange,
  onImport,
  importedAt
}: ExtractionPanelProps) {
  const result = job?.result as ExtractionDocument | undefined;

  const layers = useMemo(() => layersFromExtraction(result), [result]);
  const componentCount = useMemo(
    () => layers.reduce((total, layer) => total + layer.shapes.length, 0),
    [layers]
  );

  if (!open) {
    return null;
  }

  const rejected = isRejected(result);
  const skipped = skippedPages(result);
  const document = result?.document;
  const planPages = (result?.pages ?? []).filter((page) => page.isPlan);

  return (
    <div className="absolute inset-y-0 right-0 z-40 flex w-[380px] flex-col border-l border-edge bg-panel shadow-[-8px_0_24px_-12px_rgba(0,0,0,0.25)]">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-edge px-3">
        <p className="pane-label">Extraction</p>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close extraction panel"
          className="grid h-6 w-6 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-ink hover:text-ink"
        >
          <span aria-hidden>×</span>
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {rejected ? (
          <div className="rounded-md border border-accent bg-accent-soft p-3">
            <p className="text-xs font-semibold text-accent">Not a construction plan</p>
            <p className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
              {result?.rejection?.message}
            </p>
          </div>
        ) : null}

        {job && !rejected ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="truncate font-mono text-[11px] text-ink-faint">{job.id}</span>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                  STATUS_STYLES[job.status] ?? 'bg-chrome text-ink-soft'
                }`}
              >
                {job.status}
              </span>
            </div>

            {document ? (
              <p className="font-mono text-[11px] text-ink-soft">
                {document.pagesProcessed !== undefined && result?.partial
                  ? `Reading page ${document.pagesProcessed} of ${document.pageCount}`
                  : `${document.planPageCount ?? 0} plan page${
                      (document.planPageCount ?? 0) === 1 ? '' : 's'
                    } of ${document.pageCount ?? 0}`}
              </p>
            ) : null}

            {result?.totals && Object.keys(result.totals).length > 0 ? (
              <div className="grid grid-cols-2 gap-1.5">
                {Object.entries(result.totals).map(([layer, count]) => (
                  <div key={layer} className="rounded-md bg-chrome px-2.5 py-2">
                    <p className="font-mono text-sm text-ink">{count}</p>
                    <p className="text-[10px] uppercase tracking-wide text-ink-faint">
                      {layer}
                    </p>
                  </div>
                ))}
              </div>
            ) : null}

            {componentCount > 0 ? (
              <button
                type="button"
                onClick={() => result && onImport(result)}
                className="w-full rounded-md bg-ink px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-accent"
              >
                {importedAt ? 'Re-import' : 'Add'} {componentCount} component
                {componentCount === 1 ? '' : 's'} as layers
              </button>
            ) : null}

            {importedAt ? (
              <p className="text-[10px] text-ink-faint">
                Imported at {new Date(importedAt).toLocaleTimeString()}. Re-importing
                replaces the extracted layers and leaves your own untouched.
              </p>
            ) : null}

            {planPages.length > 0 ? (
              <div>
                <p className="pane-label">Plan pages</p>
                <ul className="mt-1.5 space-y-1">
                  {planPages.map((page) => (
                    <li
                      key={page.pageNumber}
                      className="flex items-baseline justify-between gap-2 border-b border-edge pb-1 text-[11px]"
                    >
                      <span className="text-ink">
                        p{page.pageNumber}{' '}
                        <span className="text-ink-faint">{page.planType}</span>
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-ink-soft">
                        {page.scale?.text ?? 'unknown'}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {skipped.length > 0 ? (
              <details className="rounded-md border border-edge">
                <summary className="cursor-pointer px-2.5 py-1.5 text-[11px] text-ink-soft">
                  {skipped.length} page{skipped.length === 1 ? '' : 's'} skipped
                </summary>
                <ul className="border-t border-edge px-2.5 py-2">
                  {skipped.map((page) => (
                    <li key={page.pageNumber} className="text-[10px] leading-relaxed text-ink-faint">
                      p{page.pageNumber} — {page.skippedReason}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}

            {job.error ? (
              <p className="rounded-md bg-red-50 px-2.5 py-2 text-[11px] leading-relaxed text-red-700">
                {job.error}
              </p>
            ) : null}

            {result ? (
              <details className="rounded-md border border-edge">
                <summary className="cursor-pointer px-2.5 py-1.5 text-[11px] text-ink-soft">
                  Raw result
                </summary>
                <pre className="max-h-64 overflow-auto border-t border-edge bg-chrome-2 p-2.5 font-mono text-[10px] leading-relaxed">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            ) : null}
          </div>
        ) : null}

        {message ? (
          <p className="rounded-md bg-chrome px-2.5 py-2 font-mono text-[11px] leading-relaxed text-ink-soft">
            {message}
          </p>
        ) : null}

        <label className="block">
          <span className="pane-label">Notification webhook</span>
          <input
            value={notificationUrl}
            onChange={(event) => onNotificationUrlChange(event.target.value)}
            placeholder="https://example.com/webhook"
            disabled={isRunning}
            className="mt-1.5 w-full rounded-md border border-edge px-2.5 py-1.5 font-mono text-xs text-ink outline-none transition-colors focus:border-ink disabled:bg-chrome"
          />
          <span className="mt-1 block text-[11px] text-ink-faint">
            Optional. The worker calls it when the job finishes.
          </span>
        </label>

        {!job ? (
          <p className="text-[11px] leading-relaxed text-ink-faint">
            No job yet. Use “Run extraction” in the top bar to read this drawing set for
            walls, doors, windows, columns and rooms.
          </p>
        ) : null}
      </div>
    </div>
  );
}
