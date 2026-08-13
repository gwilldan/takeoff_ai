type Feature = {
  title: string;
  body: string;
  wide?: boolean;
};

const FEATURES: Feature[] = [
  {
    title: 'Layers, not a black box',
    body: 'Quantities are attached to shapes you can select, move, and delete. If a number looks wrong, you can point at the geometry that produced it — which is what makes a takeoff defensible in a review meeting.',
    wide: true
  },
  {
    title: 'Scale calibration',
    body: 'Draw a line over any known dimension, type its real length, and every measurement on the sheet re-derives instantly.'
  },
  {
    title: 'Line and area tools',
    body: 'Polylines total their length; closed regions total their area with the shoelace formula. Both read in mm, m, or m².'
  },
  {
    title: 'Vector-accurate geometry',
    body: 'Measurements come from the PDF’s own line segments and coordinates, not from pixels guessed off a raster image.'
  },
  {
    title: 'Sheet-set navigation',
    body: 'Jump between pages from a thumbnail rail, and filter down to only the sheets that carry annotations.'
  }
];

export function Features() {
  return (
    <section id="features" className="bg-paper">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <p className="rule-label">What you get</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          Built for the part everyone skips: checking the numbers.
        </h2>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => (
            <article
              key={feature.title}
              className={`rounded-xl border border-rule bg-paper-2 p-6 transition-colors hover:border-rule-strong ${
                feature.wide ? 'sm:col-span-2 lg:col-span-2 lg:row-span-1' : ''
              }`}
            >
              <h3
                className={`font-semibold text-ink ${feature.wide ? 'text-xl' : 'text-base'}`}
              >
                {feature.title}
              </h3>
              <p
                className={`mt-3 leading-relaxed text-ink-soft ${
                  feature.wide ? 'text-base max-w-xl' : 'text-sm'
                }`}
              >
                {feature.body}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
