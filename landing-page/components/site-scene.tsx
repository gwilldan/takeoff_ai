/**
 * A construction site drawn as line art, for use as a faded backdrop.
 *
 * Deliberately not a photograph: line work matches the drawing-office
 * aesthetic used across the site, carries no licensing baggage, stays crisp at
 * any size, and adds nothing to the page payload.
 *
 * Strokes use `currentColor`, so the parent sets the colour and the opacity.
 */
export function SiteScene({ className, show }: { className?: string, show?: boolean }) {
  return (
    <svg
      viewBox="0 0 1200 420"
      preserveAspectRatio="xMidYMax slice"
      className={className}
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="square"
    >
      {/* Ground */}
      <line x1="0" y1="392" x2="1200" y2="392" strokeWidth="2" />

      {/* Tower crane, left */}
      <g>
        {/* Mast with lattice bracing */}
        <line x1="188" y1="392" x2="188" y2="74" />
        <line x1="214" y1="392" x2="214" y2="74" />
        {Array.from({ length: 11 }, (_, index) => 92 + index * 28).map((y) => (
          <g key={`mast-${y}`}>
            <line x1="188" y1={y} x2="214" y2={y} />
            <line x1="188" y1={y} x2="214" y2={y + 28} />
          </g>
        ))}
        {/* Jib and counter-jib */}
        <line x1="96" y1="74" x2="470" y2="74" />
        <line x1="96" y1="88" x2="470" y2="88" />
        {Array.from({ length: 13 }, (_, index) => 96 + index * 29).map((x) => (
          <line key={`jib-${x}`} x1={x} y1="74" x2={x + 29} y2="88" />
        ))}
        {/* Operator cab */}
        <rect x="190" y="90" width="24" height="20" />
        {/* Tie bars */}
        <line x1="201" y1="42" x2="452" y2="74" />
        <line x1="201" y1="42" x2="112" y2="74" />
        <line x1="201" y1="42" x2="201" y2="74" />
        {/* Hoist rope and load */}
        <line x1="392" y1="88" x2="392" y2="236" />
        <rect x="374" y="236" width="36" height="22" />
        {/* Counterweight */}
        <rect x="104" y="88" width="34" height="26" />
      </g>

      {/* Structural frame under construction, centre */}
      {show && <g>
        <rect x="540" y="176" width="212" height="216" />
        {/* Floor slabs */}
        {[224, 272, 320, 368].map((y) => (
          <line key={`slab-a-${y}`} x1="540" y1={y} x2="752" y2={y} />
        ))}
        {/* Columns */}
        {[593, 646, 699].map((x) => (
          <line key={`col-a-${x}`} x1={x} y1="176" x2={x} y2="392" />
        ))}
        
        {/* Scaffolding on the near face */}
        <g strokeWidth="0.9">
          {[556, 584, 612, 640, 668, 696, 724].map((x) => (
            <line key={`scaff-v-${x}`} x1={x} y1="164" x2={x} y2="392" />
          ))}
          {[196, 244, 292, 340, 388].map((y) => (
            <line key={`scaff-h-${y}`} x1="548" y1={y} x2="740" y2={y} />
          ))}
          {/* Diagonal bracing */}
          <line x1="556" y1="196" x2="612" y2="244" />
          <line x1="668" y1="292" x2="724" y2="340" />
        </g>
      </g>
}
      {/* Completed block, right */}
      {show && <g>
        <rect x="824" y="124" width="168" height="268" />
        {[172, 220, 268, 316, 364].map((y) => (
          <line key={`slab-b-${y}`} x1="824" y1={y} x2="992" y2={y} />
        ))}
        {/* Window bays */}
        {[848, 890, 932, 966].map((x) => (
          <line key={`win-b-${x}`} x1={x} y1="124" x2={x} y2="392" strokeWidth="0.8" />
        ))}
      </g>}

      {/* Low-rise block and site hut, far right */}
      {show && <g>
        <rect x="1024" y="272" width="140" height="120" />
        {[312, 352].map((y) => (
          <line key={`slab-c-${y}`} x1="1024" y1={y} x2="1164" y2={y} />
        ))}
        <line x1="1070" y1="272" x2="1070" y2="392" strokeWidth="0.8" />
        <line x1="1118" y1="272" x2="1118" y2="392" strokeWidth="0.8" />
      </g>}

      {/* Mobile crane, foreground left */}
      <g>
        <line x1="286" y1="392" x2="286" y2="330" />
        <line x1="286" y1="330" x2="416" y2="212" />
        <line x1="292" y1="336" x2="422" y2="218" />
        <line x1="419" y1="215" x2="419" y2="268" />
        <rect x="404" y="268" width="30" height="18" />
        <rect x="262" y="356" width="58" height="24" />
        <circle cx="278" cy="386" r="7" />
        <circle cx="306" cy="386" r="7" />
      </g>

      {/* Site hoarding along the front */}
      <g strokeWidth="0.9">
        <line x1="0" y1="368" x2="140" y2="368" />
        <line x1="0" y1="392" x2="140" y2="392" />
        {[20, 48, 76, 104, 132].map((x) => (
          <line key={`hoard-${x}`} x1={x} y1="368" x2={x} y2="392" />
        ))}
      </g>

      {/* Stacked material, foreground */}
      <g strokeWidth="0.9">
        <rect x="458" y="360" width="56" height="14" />
        <rect x="458" y="374" width="56" height="14" />
        <rect x="466" y="346" width="40" height="14" />
      </g> 
    </svg>
  );
}
