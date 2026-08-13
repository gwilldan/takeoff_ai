import { WorkspacePreview } from './workspace-preview';

export function Hero() {
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
  const contactEmail = process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? 'hello@takeoff.ai';

  return (
    <section id="top" className="relative overflow-hidden">
      <div className="blueprint-grid absolute inset-0 opacity-70" aria-hidden />
      <div
        className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-b from-transparent to-paper"
        aria-hidden
      />

      <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-16 md:pt-28">
        <p className="rule-label">Quantity takeoff, automated</p>

        <h1 className="mt-5 max-w-4xl text-4xl font-semibold tracking-tight text-ink sm:text-5xl md:text-display">
          Turn plan PDFs into measured{' '}
          <span className="relative whitespace-nowrap">
            <span className="relative z-10">annotation layers</span>
            <span
              aria-hidden
              className="absolute inset-x-0 bottom-1 z-0 h-3 bg-accent/25 md:bottom-2 md:h-4"
            />
          </span>{' '}
          you can check.
        </h1>

        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-soft">
          Takeoff AI reads a drawing&apos;s text, vector geometry, and scale, then hands you
          editable line and area layers with live quantities. Every number traces back to a
          shape on the sheet, so review takes minutes instead of an afternoon with a scale
          rule.
        </p>

        <div className="mt-9 flex flex-wrap items-center gap-3">
          <a
            href={appUrl}
            className="rounded-lg bg-accent px-5 py-3 text-sm font-medium text-paper-2 shadow-[0_10px_24px_-12px_rgba(228,87,46,0.9)] transition-transform hover:-translate-y-0.5"
          >
            Open the workspace
          </a>
          <a
            href={`mailto:${contactEmail}?subject=Takeoff%20AI%20demo`}
            className="rounded-lg border border-rule-strong px-5 py-3 text-sm font-medium text-ink transition-colors hover:border-ink hover:bg-paper-3"
          >
            Request a demo
          </a>
          <p className="font-mono text-xs text-ink-faint">
            Upload a PDF · no account required locally
          </p>
        </div>

        <div className="mt-14">
          <WorkspacePreview />
        </div>
      </div>
    </section>
  );
}
