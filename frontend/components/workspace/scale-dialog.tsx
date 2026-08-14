'use client';

import { useEffect, useState } from 'react';
import { calibrationFromLine, type LengthUnit } from '../../lib/annotations/measure';
import type { Calibration } from '../../lib/annotations/types';

export type ScaleDialogProps = {
  drawnLengthPts: number;
  onCancel: () => void;
  onConfirm: (calibration: Calibration) => void;
};

export function ScaleDialog({ drawnLengthPts, onCancel, onConfirm }: ScaleDialogProps) {
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState<LengthUnit>('mm');
  const [error, setError] = useState('');

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onCancel();
      }
    };

    window.addEventListener('keydown', onKeyDown);

    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onCancel]);

  function handleSubmit() {
    const parsed = Number(value);

    if (!value.trim() || !Number.isFinite(parsed)) {
      setError('Enter a number.');
      return;
    }

    const calibration = calibrationFromLine(drawnLengthPts, parsed, unit);

    if (!calibration) {
      setError('The real length must be greater than zero.');
      return;
    }

    onConfirm(calibration);
  }

  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-ink/25 p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Set drawing scale"
        className="w-full max-w-sm rounded-xl border border-edge bg-panel p-5 shadow-[0_20px_50px_-20px_rgba(0,0,0,0.4)]"
      >
        <p className="pane-label">Set drawing scale</p>
        <h2 className="mt-2 text-base font-semibold text-ink">
          How long is the line you just drew?
        </h2>
        <p className="mt-2 text-xs leading-relaxed text-ink-soft">
          You measured <span className="font-mono text-ink">{drawnLengthPts.toFixed(1)} pt</span>{' '}
          on the sheet. Enter that dimension&apos;s real length and every measurement in the
          document re-derives from it.
        </p>

        <div className="mt-4 flex gap-2">
          <input
            autoFocus
            inputMode="decimal"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              setError('');
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                handleSubmit();
              }
            }}
            placeholder="e.g. 12000"
            className="min-w-0 flex-1 rounded-md border border-edge px-3 py-2 font-mono text-sm outline-none transition-colors focus:border-ink"
          />
          <div className="flex overflow-hidden rounded-md border border-edge">
            {(['mm', 'm'] as LengthUnit[]).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setUnit(option)}
                aria-pressed={unit === option}
                className={`px-3 py-2 font-mono text-xs transition-colors ${
                  unit === option ? 'bg-ink text-white' : 'text-ink-soft hover:bg-chrome'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        {error ? <p className="mt-2 text-xs text-accent">{error}</p> : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-edge px-3 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:border-ink hover:text-ink"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:brightness-95"
          >
            Set scale
          </button>
        </div>
      </div>
    </div>
  );
}
