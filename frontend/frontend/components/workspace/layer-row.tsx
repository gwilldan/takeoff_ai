'use client';

import { useEffect, useState } from 'react';
import type { Layer } from '../../lib/annotations/types';
import { LAYER_PALETTE } from '../../lib/annotations/types';

export type LayerRowProps = {
  layer: Layer;
  active: boolean;
  total: string;
  onActivate: () => void;
  onRename: (name: string) => void;
  onColorChange: (color: string) => void;
  onToggleVisibility: () => void;
  onDelete: () => void;
};

export function LayerRow({
  layer,
  active,
  total,
  onActivate,
  onRename,
  onColorChange,
  onToggleVisibility,
  onDelete
}: LayerRowProps) {
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(layer.name);
  const [swatchOpen, setSwatchOpen] = useState(false);

  // Keep the draft in step when the name changes from outside this row.
  useEffect(() => {
    setDraftName(layer.name);
  }, [layer.name]);

  function commitRename() {
    const trimmed = draftName.trim();

    if (trimmed && trimmed !== layer.name) {
      onRename(trimmed);
    } else {
      setDraftName(layer.name);
    }

    setEditing(false);
  }

  return (
    <li
      onClick={onActivate}
      className={`group relative rounded-md border px-2 py-2 transition-colors ${
        active ? 'border-ink bg-chrome' : 'border-transparent hover:bg-chrome-2'
      }`}
    >
      <div className="flex items-start gap-2">
        <button
          type="button"
          aria-label={`Change ${layer.name} colour`}
          onClick={(event) => {
            event.stopPropagation();
            setSwatchOpen((open) => !open);
          }}
          className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-[3px] ring-1 ring-black/10"
          style={{ backgroundColor: layer.color }}
        />

        <div className="min-w-0 flex-1">
          {editing ? (
            <input
              autoFocus
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              onBlur={commitRename}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  commitRename();
                }
                if (event.key === 'Escape') {
                  setDraftName(layer.name);
                  setEditing(false);
                }
              }}
              onClick={(event) => event.stopPropagation()}
              className="w-full rounded border border-ink bg-white px-1 py-0.5 text-xs outline-none"
            />
          ) : (
            <button
              type="button"
              onDoubleClick={(event) => {
                event.stopPropagation();
                setEditing(true);
              }}
              className="block w-full truncate text-left text-xs leading-tight text-ink"
              title={`${layer.name} — double-click to rename`}
            >
              {layer.name}
            </button>
          )}

          <p className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-ink-faint">
            {layer.source === 'extraction' ? (
              <span
                title="Created by the extraction agent — editable like any other layer"
                className="rounded bg-accent-soft px-1 text-[9px] font-medium text-accent"
              >
                agent
              </span>
            ) : null}
            <span>
              {layer.kind === 'line' ? 'Length' : 'Area'} · {layer.shapes.length}{' '}
              {layer.shapes.length === 1 ? 'shape' : 'shapes'}
            </span>
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <span className="rounded bg-chrome px-1.5 py-0.5 font-mono text-[10px] text-ink-soft">
            {total}
          </span>
          <button
            type="button"
            aria-label={layer.visible ? `Hide ${layer.name}` : `Show ${layer.name}`}
            aria-pressed={!layer.visible}
            onClick={(event) => {
              event.stopPropagation();
              onToggleVisibility();
            }}
            className={`grid h-5 w-5 place-items-center rounded text-[11px] transition-colors ${
              layer.visible ? 'text-ink-soft hover:text-ink' : 'text-ink-faint'
            }`}
          >
            <span aria-hidden>{layer.visible ? '◉' : '◌'}</span>
          </button>
          <button
            type="button"
            aria-label={`Delete ${layer.name}`}
            onClick={(event) => {
              event.stopPropagation();
              onDelete();
            }}
            className="grid h-5 w-5 place-items-center rounded text-[11px] text-ink-faint opacity-0 transition-opacity hover:text-accent group-hover:opacity-100"
          >
            <span aria-hidden>×</span>
          </button>
        </div>
      </div>

      {swatchOpen ? (
        <div
          className="mt-2 flex flex-wrap gap-1.5 rounded-md border border-edge bg-panel p-1.5"
          onClick={(event) => event.stopPropagation()}
        >
          {LAYER_PALETTE.map((color) => (
            <button
              key={color}
              type="button"
              aria-label={`Use ${color}`}
              onClick={() => {
                onColorChange(color);
                setSwatchOpen(false);
              }}
              className={`h-4 w-4 rounded-[3px] ring-1 ring-black/10 ${
                color === layer.color ? 'outline-2 outline-offset-1 outline-ink' : ''
              }`}
              style={{ backgroundColor: color }}
            />
          ))}
        </div>
      ) : null}
    </li>
  );
}
