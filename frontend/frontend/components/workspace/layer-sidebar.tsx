'use client';

import { useMemo } from 'react';
import { layerTotal } from '../../lib/annotations/measure';
import { useAnnotations } from '../../lib/annotations/store';
import type { ShapeKind } from '../../lib/annotations/types';
import { LayerRow } from './layer-row';

export function LayerSidebar() {
  const { state, dispatch } = useAnnotations();

  const totals = useMemo(
    () => new Map(state.layers.map((layer) => [layer.id, layerTotal(layer, state.calibration)])),
    [state.layers, state.calibration]
  );

  function addLayer(kind: ShapeKind) {
    dispatch({ type: 'addLayer', id: crypto.randomUUID(), kind });
  }

  return (
    <aside className="flex min-h-0 flex-col border-r border-edge bg-panel">
      <div className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <p className="pane-label">Layers</p>
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

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {state.layers.length === 0 ? (
          <p className="px-1 text-[11px] leading-relaxed text-ink-faint">
            No layers yet. Pick the line or area tool and start drawing — a layer is created
            for you — or add one above.
          </p>
        ) : (
          <ul className="space-y-1">
            {state.layers.map((layer) => (
              <LayerRow
                key={layer.id}
                layer={layer}
                active={layer.id === state.activeLayerId}
                total={totals.get(layer.id) ?? ''}
                onActivate={() => dispatch({ type: 'setActiveLayer', layerId: layer.id })}
                onRename={(name) => dispatch({ type: 'renameLayer', layerId: layer.id, name })}
                onColorChange={(color) =>
                  dispatch({ type: 'setLayerColor', layerId: layer.id, color })
                }
                onToggleVisibility={() =>
                  dispatch({ type: 'toggleLayerVisibility', layerId: layer.id })
                }
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
