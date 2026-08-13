type Step = {
  index: string;
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    index: '01',
    title: 'Upload the drawing set',
    body: 'Drop in a plan PDF. Pages render locally in the browser, so you see the set before anything leaves your machine.'
  },
  {
    index: '02',
    title: 'Extraction runs in the background',
    body: 'The worker pulls text spans, line segments, and curves from each page, infers the drawing scale, and identifies walls, rooms, and openings.'
  },
  {
    index: '03',
    title: 'Review the layers',
    body: 'Results land as annotation layers over the sheet. Draw what was missed, delete what was wrong, and recalibrate the scale from any known dimension.'
  },
  {
    index: '04',
    title: 'Take the quantities',
    body: 'Every layer carries a live total — length for line layers, area for regions — computed from the geometry you can see on the page.'
  }
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="border-y border-rule bg-paper-2">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <p className="rule-label">How it works</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          Four steps from a drawing set to a defensible quantity.
        </h2>

        <ol className="mt-14 grid gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step) => (
            <li key={step.index} className="relative border-t border-rule pt-5">
              <span aria-hidden className="absolute -top-px left-0 h-px w-10 bg-accent" />
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
