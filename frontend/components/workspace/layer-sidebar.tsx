'use client';

import { useMemo } from 'react';

export function LayerSidebar() {

  return (
    <aside className="flex min-h-0 flex-col border-r border-edge bg-panel">
      <div className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <p className="pane-label">Layers</p>
        <div className="flex gap-1">
          <button
            type="button"
            title="Add a line layer"
            className="rounded border border-edge px-1.5 py-0.5 font-mono text-[10px] text-ink-soft transition-colors hover:border-ink hover:text-ink"
          >
            + line
          </button>
          <button
            type="button"
            title="Add an area layer"
            className="rounded border border-edge px-1.5 py-0.5 font-mono text-[10px] text-ink-soft transition-colors hover:border-ink hover:text-ink"
          >
            + area
          </button>
        </div>
      </div>

    </aside>
  );
}
