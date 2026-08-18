import Image from "next/image";

const NAV_LINKS = [
  { href: '#how-it-works', label: 'How it works' },
  { href: '#features', label: 'Features' },
  { href: '#capabilities', label: 'Capabilities' }
];

export function SiteHeader() {
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';

  return (
    <header className="sticky top-0 z-50 border-b border-rule/70 bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-6 px-6">
        <a href="#top" className="flex items-center gap-2.5">
          <Image src="/takeoffai-icon.svg" alt="icon" width={28} height={28} />
          <span className="text-[15px] font-semibold tracking-tight text-ink">
            Takeoff<span className="text-accent">AI</span>
          </span>
        </a>

        <nav className="hidden md:flex items-center gap-8 ">
          {NAV_LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="group relative text-sm text-ink-soft transition-colors hover:text-ink focus-visible:text-ink focus-visible:outline-none mx-2"
            >
              <span>{link.label}</span>
              <span
                aria-hidden
                className="absolute -bottom-1.5 left-0 h-[1.5px] w-full scale-x-0 bg-accent transition-transform duration-300 ease-out group-hover:scale-x-100 group-focus-visible:scale-x-100 motion-reduce:transition-none"
              />
            </a>
          ))}
        </nav>

        <a
          href={appUrl}
          target="_blank"
          className=" rounded-lg bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-accent"
        >
          Open the workspace
        </a>
      </div>
    </header>
  );
}
