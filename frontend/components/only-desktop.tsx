export default function OnlyDesktop() {
  return (
    <main className="h-dvh w-dvw grid place-content-center canvas-field ">
         <div className=" bg-white grid place-content-center p-8 text-center w-[85dvw] rounded-3xl ">
        <h2 className="mt-3 text-lg font-semibold text-ink">ONLY DESKTOP VIEW</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
            Please switch to a desktop or laptop to continue.
        </p>

        <button
          type="button"
          className="mt-6 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent"
        >
          Check our Home page
        </button>
      </div>
    </main>
  )
}
