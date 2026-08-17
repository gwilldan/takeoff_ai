'use client';

import Image from 'next/image';
import { useRef } from 'react';

export type TopBarProps = {
  fileName: string | null;
  scaleText: string;
  onOpenFile: (file: File) => void;
  onRunExtraction?: () => void;
  extractionLabel?: string;
  extractionBusy?: boolean;
  onToggleResults?: () => void;
  resultsBadge?: string | null;
};

export function TopBar({
  fileName,
  scaleText,
  onOpenFile,
  onRunExtraction,
  extractionLabel = 'Run extraction',
  extractionBusy = false,
  onToggleResults,
  resultsBadge
}: TopBarProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-edge bg-panel px-4">
      <div className="flex items-center gap-2">
        <Image src="/takeoffai-icon.svg" alt="icon" width={28} height={28} />
        <span className="text-sm font-semibold tracking-tight">
          Takeoff<span className="text-accent">AI</span>
        </span>
      </div>

      <span className="h-5 w-px bg-edge" aria-hidden />

      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="rounded-md border border-edge-strong px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:border-ink"
        >
          Open PDF
        </button>
        {fileName ? (
          <span className="truncate font-mono text-xs text-ink-soft">{fileName}</span>
        ) : (
          <span className="font-mono text-xs text-ink-faint">No document</span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-3">
        <span className="rounded-md bg-chrome px-2 py-1 font-mono text-[11px] text-ink-soft">
          {scaleText}
        </span>

        {onToggleResults ? (
          <button
            type="button"
            onClick={onToggleResults}
            className="relative rounded-md border border-edge-strong px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:border-ink"
          >
            Results
            {resultsBadge ? (
              <span className="ml-1.5 rounded bg-accent-soft px-1 font-mono text-[10px] text-accent">
                {resultsBadge}
              </span>
            ) : null}
          </button>
        ) : null}

        {onRunExtraction ? (
          <button
            type="button"
            onClick={onRunExtraction}
            disabled={extractionBusy || !fileName}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:bg-edge-strong disabled:text-ink-faint"
          >
            {extractionLabel}
          </button>
        ) : null}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            onOpenFile(file);
          }
          event.target.value = '';
        }}
      />
    </header>
  );
}
