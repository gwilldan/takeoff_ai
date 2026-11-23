'use client';

import { useMemo, useState } from 'react';
import { saveTrainingLabels, type JobRecord } from '../../lib/api';
import {
  isRejected,
  layersFromExtraction,
  trainingPagesFromLayers,
  type TakeoffDocument
} from '../../lib/annotations/extraction-import';
import { useAnnotations } from '../../lib/annotations/store';

export type ExtractionPanelProps = {
  open: boolean;
  onClose: () => void;
  job: JobRecord | null;
  message: string;
  isRunning: boolean;
  notificationUrl: string;
  onNotificationUrlChange: (value: string) => void;
  onImport: (result: TakeoffDocument) => void;
  importedAt: string | null;
  /** Page on screen, highlighted in the plan page list. */
  currentPage: number;
  onGoToPage: (pageNumber: number) => void;
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
  importedAt,
  currentPage,
  onGoToPage
}: ExtractionPanelProps) {
  const result = job?.result;
  const { state } = useAnnotations();
  const [training, setTraining] = useState<{ busy: boolean; note: string }>({ busy: false, note: '' });

  const layers = useMemo(() => layersFromExtraction(result), [result]);

  const saveForTraining = async () => {
    if (!job) {
      return;
    }

    setTraining({ busy: true, note: '' });
    try {
      const saved = await saveTrainingLabels(job.id, trainingPagesFromLayers(result, state.layers));
      setTraining({
        busy: false,
        note: `Saved ${saved.detections} shapes on ${saved.pages} page${saved.pages === 1 ? '' : 's'} for training.`
      });
    } catch (error) {
      setTraining({ busy: false, note: error instanceof Error ? error.message : 'Saving for training failed.' });
    }
  };
  const componentCount = useMemo(
    () => layers.reduce((total, layer) => total + layer.shapes.length, 0),
    [layers]
  );

  if (!open) {
    return null;
  }

  const rejected = isRejected(result);
  const skipped = (result?.pages ?? []).filter((page) => !page.is_plan);
  const document = result?.document;
  const planPages = (result?.pages ?? []).filter((page) => page.is_plan);
  const metrics = result?.metrics;
  const seconds = metrics?.seconds;

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
                {result?.status === 'running'
                  ? `${document.pages_processed} of ${document.page_count} pages done`
                  : `${document.plan_page_count} floor plan page${
                      document.plan_page_count === 1 ? '' : 's'
                    } of ${document.page_count}`}
              </p>
            ) : null}

            {metrics?.counts && Object.keys(metrics.counts).length > 0 ? (
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  ...Object.entries(metrics.counts).map(([name, count]) => [name, String(count)]),
                  ['wall length', `${metrics.wall_length_m ?? 0} m`],
                  ['room area', `${metrics.room_area_m2 ?? 0} m²`]
                ].map(([name, value]) => (
                  <div key={name} className="rounded-md bg-chrome px-2.5 py-2">
                    <p className="font-mono text-sm text-ink">{value}</p>
                    <p className="text-[10px] uppercase tracking-wide text-ink-faint">{name}</p>
                  </div>
                ))}
              </div>
            ) : null}

            {seconds?.takeoff_mean != null ? (
              <p className="font-mono text-[10px] leading-relaxed text-ink-faint">
                Classify {seconds.read_mean ?? '–'} s/page · takeoff {seconds.takeoff_mean} s/page (max{' '}
                {seconds.takeoff_max} s)
                {result?.timings?.elapsed_s != null ? ` · whole job ${result.timings.elapsed_s} s` : ''}
              </p>
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

            {importedAt && job.status === 'completed' ? (
              <div className="space-y-1.5">
                <button
                  type="button"
                  onClick={saveForTraining}
                  disabled={training.busy}
                  className="w-full rounded-md border border-ink px-3 py-2 text-xs font-medium text-ink transition-colors hover:bg-chrome disabled:opacity-50"
                >
                  {training.busy ? 'Saving…' : 'Save corrections for training'}
                </button>
                <p className="text-[10px] leading-relaxed text-ink-faint">
                  {training.note ||
                    'Correct the extracted layers first: what you save here is what the model learns. Saving again replaces it.'}
                </p>
              </div>
            ) : null}

            {planPages.length > 0 ? (
              <div>
                <p className="pane-label">Plan pages</p>
                <ul className="mt-1.5 space-y-1">
                  {planPages.map((page) => {
                    const pageNumber = page.page_index + 1;
                    const onScreen = pageNumber === currentPage;
                    const tally = new Map<string, number>();
                    for (const detection of page.detections) {
                      tally.set(detection.class_name, (tally.get(detection.class_name) ?? 0) + 1);
                    }
                    const counts = page.error
                      ? page.error
                      : page.image_size
                        ? [...tally].map(([name, count]) => `${count} ${name}`).join(' · ') +
                          (page.timings?.takeoff_s != null ? ` · ${page.timings.takeoff_s} s` : '')
                        : 'Measuring…';
                    // The scale the page was measured at: ours where it replaced their estimate.
                    const scale =
                      page.scale_used !== 'ours' && page.scale?.denominator
                        ? `1:${Math.round(page.scale.denominator)}`
                        : (page.scale_check?.text ?? 'unknown');

                    return (
                      <li key={pageNumber}>
                        <button
                          type="button"
                          onClick={() => onGoToPage(pageNumber)}
                          aria-current={onScreen ? 'page' : undefined}
                          title={`Go to page ${pageNumber}`}
                          className={`w-full rounded-md border px-2 py-1.5 text-left text-[11px] transition-colors ${
                            onScreen
                              ? 'border-ink bg-chrome'
                              : 'border-edge hover:border-ink hover:bg-chrome'
                          }`}
                        >
                          <span className="flex items-baseline justify-between gap-2">
                            <span className="text-ink">
                              p{pageNumber}{' '}
                              <span className="text-ink-faint">{page.plan_type}</span>
                            </span>
                            <span className="shrink-0 font-mono text-[10px] text-ink-soft">{scale}</span>
                          </span>
                          <span
                            className={`mt-0.5 block font-mono text-[10px] ${page.error ? 'text-red-700' : 'text-ink-faint'}`}
                          >
                            {counts}
                          </span>
                        </button>
                      </li>
                    );
                  })}
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
                    <li key={page.page_index} className="text-[10px] leading-relaxed text-ink-faint">
                      p{page.page_index + 1} — {page.skipped_reason}
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
            rooms, walls, doors and windows.
          </p>
        ) : null}
      </div>
    </div>
  );
}
