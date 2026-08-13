export function Cta() {
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
  const contactEmail = process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? 'hello@takeoff.ai';

  return (
    <section className="bg-paper">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="relative isolate overflow-hidden rounded-2xl border border-ink bg-ink px-8 py-20 text-center sm:px-16">
          {/* Backdrop: site line art, then a gradient that fades it out towards
              the top so the headline keeps its contrast. */}
          <div
            className="pointer-events-none absolute inset-0 bg-gradient-to-t from-ink/5 via-ink/5 to-ink/5"
            aria-hidden
          />

          <div className="relative">
            <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-paper sm:text-4xl">
              Open a drawing and draw your first layer.
            </h2>
            <p className="mx-auto mt-5 max-w-lg text-base leading-relaxed text-paper/70">
              The workspace runs locally. Point it at a plan PDF, calibrate the scale, and
              see the quantities come out.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <a
                href={appUrl}
                className="rounded-lg bg-accent px-6 py-3 text-sm font-medium text-paper-2 transition-transform hover:-translate-y-0.5"
              >
                Open the workspace
              </a>
              <a
                href={`mailto:${contactEmail}?subject=Takeoff%20AI%20demo`}
                className=" bg-ink rounded-lg border border-paper/30 px-6 py-3 text-sm font-medium text-paper transition-colors hover:border-accent"
              >
                Request a demo
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
