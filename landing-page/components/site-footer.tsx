import Image from "next/image";

export function SiteFooter() {
  const contactEmail = process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? 'support@takeoffai.xyz';

  return (
    <footer className="border-t border-rule bg-paper-2">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <Image src="/takeoffai-icon.svg" alt="icon" width={28} height={28} />
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
