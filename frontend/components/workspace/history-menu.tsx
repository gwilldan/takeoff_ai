'use client';

import { useState, useSyncExternalStore } from 'react';
import { HISTORY_LIMIT } from '../../lib/annotations/history';
import { useAnnotations } from '../../lib/annotations/store';
import { Tooltip } from '../ui/tooltip';

const buttonClass =
  'grid h-7 w-7 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-edge-strong hover:text-ink disabled:opacity-40';

function time(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** Undo, redo, and the last versions to jump back to. */
export function HistoryMenu() {
  const { dispatch, versions, current, redoCount } = useAnnotations();
  const [open, setOpen] = useState(false);
  // The platform never changes while the page is open; the server renders Ctrl.
  const mod = useSyncExternalStore(
    () => () => {},
    () => (/Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl+'),
    () => 'Ctrl+'
  );

  const undoLabel = `Undo (${mod}Z)`;
  const redoLabel = `Redo (${mod === '⌘' ? '⇧⌘Z' : 'Ctrl+Y'})`;

  return (
    <div className="relative flex items-center gap-1">
      <Tooltip label={undoLabel}>
        <button type="button" aria-label={undoLabel} disabled={versions.length === 0}
          onClick={() => dispatch({ type: 'undo' })} className={buttonClass}>
          <span aria-hidden>↶</span>
        </button>
      </Tooltip>
      <Tooltip label={redoLabel}>
        <button type="button" aria-label={redoLabel} disabled={redoCount === 0}
          onClick={() => dispatch({ type: 'redo' })} className={buttonClass}>
          <span aria-hidden>↷</span>
        </button>
      </Tooltip>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="rounded-md border border-edge px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:border-edge-strong hover:text-ink"
      >
        History
      </button>

      {open ? (
        <div className="absolute left-0 top-9 z-50 w-64 rounded-md border border-edge bg-panel p-1 shadow-lg">
          <p className="px-2 py-1 text-[10px] text-ink-faint">Last {HISTORY_LIMIT} edits. Pick one to go back to it.</p>
          <ul className="max-h-72 overflow-y-auto">
            <li className="flex items-baseline justify-between rounded px-2 py-1.5 text-[11px] text-ink">
              <span>{current.label} <span className="text-ink-faint">(now)</span></span>
              <span className="font-mono text-[10px] text-ink-faint">{time(current.at)}</span>
            </li>
            {versions.map((version, index) => ({ version, index })).reverse().map(({ version, index }) => (
              <li key={`${version.at}:${index}`}>
                <button
                  type="button"
                  onClick={() => {
                    dispatch({ type: 'restore', index });
                    setOpen(false);
                  }}
                  className="flex w-full items-baseline justify-between rounded px-2 py-1.5 text-left text-[11px] text-ink-soft hover:bg-chrome hover:text-ink"
                >
                  <span>{version.label}</span>
                  <span className="font-mono text-[10px] text-ink-faint">{time(version.at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
