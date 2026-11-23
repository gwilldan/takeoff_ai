import type { ReactNode } from 'react';

/**
 * A label under its control, shown on hover or keyboard focus after a short
 * pause. The control keeps its own aria-label; the tooltip is visual only.
 */
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span
        aria-hidden
        className="pointer-events-none absolute top-full left-1/2 z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-ink px-1.5 py-0.5 text-[10px] font-medium text-white opacity-0 shadow-md transition-opacity delay-0 group-focus-within/tip:opacity-100 group-focus-within/tip:delay-300 group-hover/tip:opacity-100 group-hover/tip:delay-300"
      >
        {label}
      </span>
    </span>
  );
}
