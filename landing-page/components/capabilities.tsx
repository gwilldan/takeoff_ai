type Capability = {
  label: string;
  value: string;
};

const CAPABILITIES: Capability[] = [
  { label: 'Input', value: 'Multi-page AEC PDFs (50MB max), stored securely.' },
  { label: 'Read from each page', value: 'Agent extracts text, line segments, curves, coordinates' },
  { label: 'Derived automatically', value: 'Agent computes required length, area and volume' },
  { label: 'Annotation ', value: 'Extracted elements show on PDF for verification' },
  { label: 'Output ', value: 'A documented Bill of Quantities, exportable in major formats.' }
];

export function Capabilities() {
  return (
    <section id="capabilities" className="border-y border-rule bg-paper-2">
      <div className="mx-auto grid max-w-6xl gap-12 px-6 py-20 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)]">
        <div>
          <p className="rule-label">Capabilities</p>
          <h2 className="mt-4 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            Exactly what the pipeline handles.
          </h2>
          <p className="mt-5 max-w-md text-base leading-relaxed text-ink-soft">
            What the pipeline handles is exactly what&apos;s on your sheet, nothing assumed. Upload one and check the totals yourself.
          </p>
        </div>

        <dl className="divide-y divide-rule">
          {CAPABILITIES.map((capability) => (
            <div
              key={capability.label}
              className="grid gap-1 py-4 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)] sm:gap-6"
            >
              <dt className="rule-label pt-0.5">{capability.label}</dt>
              <dd className="text-sm leading-relaxed text-ink">{capability.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
