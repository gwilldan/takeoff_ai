'use client';

import { useEffect, useMemo, useState } from 'react';
import { layerTotal } from '../../lib/annotations/measure';
import { useAnnotations } from '../../lib/annotations/store';
import type { ShapeKind } from '../../lib/annotations/types';
import { LayerRow } from './layer-row';

export type LayerSidebarProps = {
  currentPage: number;
  /** The layer whose shapes are highlighted on the page, if any. */
  highlightedLayerId: string | null;
  onHighlight: (layerId: string | null) => void;
};

/**
 * The layers drawn on the page on screen. Layers span the whole set, so
 * totals can be read for this page or for every page; a layer with nothing
 * drawn yet is listed on every page so it can be drawn into.
 */
export function LayerSidebar({ currentPage, highlightedLayerId, onHighlight }: LayerSidebarProps) {
  const { state, dispatch } = useAnnotations();
  const [wholeSet, setWholeSet] = useState(false);

  const rows = useMemo(
    () =>
      state.layers
        .map((layer) => ({ layer, onPage: layer.shapes.filter((shape) => shape.pageNumber === currentPage) }))
        .filter(({ layer, onPage }) => onPage.length > 0 || layer.shapes.length === 0)
        .map(({ layer, onPage }) => {
          const shapes = wholeSet ? layer.shapes : onPage;
          return { layer, count: shapes.length, total: layerTotal(layer, state.calibration, shapes) };
        }),
    [currentPage, state.calibration, state.layers, wholeSet]
  );

  // A highlight belongs to the page it was made on, and Esc clears it.
  useEffect(() => {
    onHighlight(null);
  }, [currentPage, onHighlight]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onHighlight(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onHighlight]);

  function addLayer(kind: ShapeKind) {
    dispatch({ type: 'addLayer', id: crypto.randomUUID(), kind });
  }

  return (
    <aside className="flex min-h-0 flex-col border-r border-edge bg-panel">
      <div className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <p className="pane-label">Layers · page {currentPage}</p>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => addLayer('line')}
            title="Add a line layer"
            className="rounded border border-edge px-1.5 py-0.5 font-mono text-[10px] text-ink-soft transition-colors hover:border-ink hover:text-ink"
          >
            + line
          </button>
          <button
            type="button"
            onClick={() => addLayer('area')}
            title="Add an area layer"
            className="rounded border border-edge px-1.5 py-0.5 font-mono text-[10px] text-ink-soft transition-colors hover:border-ink hover:text-ink"
          >
            + area
          </button>
        </div>
      </div>

      <div className="flex shrink-0 gap-1 border-b border-edge px-3 py-1.5" role="group" aria-label="Totals for">
        {[false, true].map((whole) => (
          <button
            key={String(whole)}
            type="button"
            aria-pressed={wholeSet === whole}
            onClick={() => setWholeSet(whole)}
            className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
              wholeSet === whole ? 'bg-ink text-white' : 'text-ink-soft hover:text-ink'
            }`}
          >
            {whole ? 'All pages' : 'This page'}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {rows.length === 0 ? (
          <p className="px-1 text-[11px] leading-relaxed text-ink-faint">
            Nothing on this page yet. Pick the line or area tool and start drawing — a layer is
            created for you — or add one above.
          </p>
        ) : (
          <ul className="space-y-1">
            {rows.map(({ layer, count, total }) => (
              <LayerRow
                key={layer.id}
                layer={layer}
                count={count}
                total={total}
                active={layer.id === state.activeLayerId}
                highlighted={layer.id === highlightedLayerId}
                onActivate={() => {
                  dispatch({ type: 'setActiveLayer', layerId: layer.id });
                  onHighlight(layer.id === highlightedLayerId ? null : layer.id);
                }}
                onRename={(name) => dispatch({ type: 'renameLayer', layerId: layer.id, name })}
                onColorChange={(color) => dispatch({ type: 'setLayerColor', layerId: layer.id, color })}
                onToggleVisibility={() => dispatch({ type: 'toggleLayerVisibility', layerId: layer.id })}
                onDelete={() => dispatch({ type: 'deleteLayer', layerId: layer.id })}
              />
            ))}
          </ul>
        )}
      </div>

      {state.calibration ? null : (
        <p className="shrink-0 border-t border-edge bg-accent-soft px-3 py-2 text-[10px] leading-relaxed text-accent">
          Totals are in PDF points until you set the drawing scale with the ⌗ tool.
        </p>
      )}
    </aside>
  );
}
