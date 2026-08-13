export function SiteFooter() {
  const contactEmail = process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? 'hello@takeoff.ai';

  return (
    <footer className="border-t border-rule bg-paper-2">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid h-6 w-6 place-items-center rounded-md bg-ink font-mono text-[11px] font-bold text-paper"
          >
            T
          </span>
          <span className="text-sm font-semibold text-ink">
            Takeoff<span className="text-accent">AI</span>
          </span>
        </div>
        <p className="font-mono text-xs text-ink-faint">
          Automating civil engineering takeoffs from drawings
        </p>
        <a
          href={`mailto:${contactEmail}`}
          className="text-sm text-ink-soft transition-colors hover:text-accent"
        >
          {contactEmail}
        </a>
      </div>
    </footer>
  );
}
