type Step = {
  index: string;
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    index: '01',
    title: 'Upload Drawing set',
    body: 'Drop in a vector plan PDF. Pages render locally in the browser, then are stored securely on our server'
  },
  {
    index: '02',
    title: 'Agentic Extraction',
    body: 'The Agent pulls text spans, line segments, and curves from each page, infers the drawing data, and identifies walls, rooms, and other engineering elements.'
  },
  {
    index: '03',
    title: 'Review layers',
    body: 'Results land as annotation layers over the sheet. Recalibrate the scale using any known dimension, then review before moving on.'

  },
  {
    index: '04',
    title: 'Take Quantities',
    body: 'The Agent computes final dimensions and counts, then presents a Bill of Quantities you can export in any standard format.'
}
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="border-y border-rule bg-paper-2">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <p className="rule-label">How it works</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          Four steps from a drawing set to a defensible <span className="text-accent">Takeoff.</span>
        </h2>

        <ol className="mt-14 grid gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step, idx) => (
            <li key={step.index} className="relative border-t border-rule pt-5">
              <span style={{width: `${(idx + 1)/4 * 100}%` }} aria-hidden className="absolute -top-px left-0 h-px bg-accent" />
              <span className="font-mono text-xs text-accent">{step.index}</span>
              <h3 className="mt-3 text-base font-semibold text-ink">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
