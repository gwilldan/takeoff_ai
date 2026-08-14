'use client';

import { useRef, useState, type DragEvent } from 'react';

export function EmptyState({ onOpenFile }: { onOpenFile: (file: File) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);

    const file = event.dataTransfer.files?.[0];

    if (file && file.type === 'application/pdf') {
      onOpenFile(file);
    }
  }

  return (
    <div className="canvas-field flex h-full items-center justify-center p-8">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`w-full max-w-md rounded-xl border-2 border-dashed bg-panel/80 p-10 text-center backdrop-blur transition-colors ${
          dragging ? 'border-accent bg-accent-soft/60' : 'border-edge-strong'
        }`}
      >
        <p className="pane-label">No document open</p>
        <h2 className="mt-3 text-lg font-semibold text-ink">Drop a plan PDF here</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          Pages render in your browser. Nothing is uploaded until you start an extraction.
        </p>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-6 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent"
        >
          Choose a file
        </button>

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
      </div>
    </div>
  );
}
