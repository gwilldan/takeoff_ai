"use client";

import type { ToolId } from "../../lib/annotations/types";
import { Tooltip } from "../ui/tooltip";
import { HistoryMenu } from "./history-menu";

type ToolButton = {
  id: ToolId;
  glyph: string;
  label: string;
};

const TOOLS: ToolButton[] = [
  { id: "select", glyph: "↖", label: "Select (V)" },
  { id: "line", glyph: "⁄", label: "Line layer (L)" },
  { id: "area", glyph: "▱", label: "Area layer (A)" },
  { id: "pan", glyph: "✥", label: "Pan (H)" },
  { id: "calibrate", glyph: "⌗", label: "Set scale (S)" },
];

export type ViewerToolbarProps = {
  tool: ToolId;
  onToolChange: (tool: ToolId) => void;
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  currentPage: number;
  pageCount: number;
  onPageChange: (pageNumber: number) => void;
  scaleText: string;
};

export function ViewerToolbar({
  tool,
  onToolChange,
  zoom,
  onZoomIn,
  onZoomOut,
  onFitWidth,
  currentPage,
  pageCount,
  onPageChange,
  scaleText,
}: ViewerToolbarProps) {
  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-edge bg-chrome-2 px-3">
      <div className="flex items-center gap-1">
        {TOOLS.map((item) => (
          <Tooltip key={item.id} label={item.label}>
            <button
              type="button"
              aria-label={item.label}
              aria-pressed={tool === item.id}
              onClick={() => onToolChange(item.id)}
              className={`grid h-7 w-7 place-items-center rounded-md text-sm transition-colors ${
                tool === item.id
                  ? "bg-ink text-white"
                  : "border border-edge text-ink-soft hover:border-edge-strong hover:text-ink"
              }`}
            >
              <span aria-hidden>{item.glyph}</span>
            </button>
          </Tooltip>
        ))}
      </div>

      <span className="h-5 w-px bg-edge" aria-hidden />

      <HistoryMenu />

      <span className="h-5 w-px bg-edge" aria-hidden />

      <div className="flex items-center gap-1">
        <Tooltip label="Zoom out">
          <button
            type="button"
            onClick={onZoomOut}
            aria-label="Zoom out"
            className="grid h-7 w-7 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-edge-strong hover:text-ink"
          >
            <span aria-hidden>−</span>
          </button>
        </Tooltip>
        <span className="w-14 text-center font-mono text-[11px] text-ink-soft">
          {Math.round(zoom * 100)}%
        </span>
        <Tooltip label="Zoom in">
          <button
            type="button"
            onClick={onZoomIn}
            aria-label="Zoom in"
            className="grid h-7 w-7 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-edge-strong hover:text-ink"
          >
            <span aria-hidden>+</span>
          </button>
        </Tooltip>
        <Tooltip label="Fit to width">
          <button
            type="button"
            onClick={onFitWidth}
            className="ml-1 rounded-md border border-edge px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:border-edge-strong hover:text-ink"
          >
            Fit
          </button>
        </Tooltip>
      </div>

      <span className="h-5 w-px bg-edge" aria-hidden />

      <div className="flex items-center gap-1">
        <Tooltip label="Previous page">
          <button
            type="button"
            onClick={() => onPageChange(currentPage - 1)}
            disabled={currentPage <= 1}
            aria-label="Previous page"
            className="grid h-7 w-7 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-edge-strong hover:text-ink disabled:opacity-40"
          >
            <span aria-hidden>‹</span>
          </button>
        </Tooltip>
        <span className="font-mono text-[11px] text-ink-soft">
          Page {currentPage} / {pageCount}
        </span>
        <Tooltip label="Next page">
          <button
            type="button"
            onClick={() => onPageChange(currentPage + 1)}
            disabled={currentPage >= pageCount}
            aria-label="Next page"
            className="grid h-7 w-7 place-items-center rounded-md border border-edge text-ink-soft transition-colors hover:border-edge-strong hover:text-ink disabled:opacity-40"
          >
            <span aria-hidden>›</span>
          </button>
        </Tooltip>
      </div>

      <span className="ml-auto font-mono text-[11px] text-ink-faint">
        {scaleText}
      </span>
    </div>
  );
}
