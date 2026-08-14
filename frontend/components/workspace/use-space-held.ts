'use client';

import { useEffect, useRef, type RefObject } from 'react';

/**
 * Tracks whether the space bar is currently down.
 *
 * Space-drag pans from any tool, which means two components need the same
 * answer: the scroll container starts panning, and the annotation overlay must
 * NOT treat the same pointerdown as a vertex. The overlay sits inside the
 * container, so its handler runs first — without this shared check, space-
 * dragging with the line tool would draw while it panned.
 *
 * Returns a ref rather than state on purpose: only pointer handlers read it,
 * and re-rendering on every space press would be pure waste.
 */
export function useSpaceHeld(): RefObject<boolean> {
  const heldRef = useRef(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === 'Space') {
        heldRef.current = true;
      }
    };

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === 'Space') {
        heldRef.current = false;
      }
    };

    // A blur while space is down would otherwise leave it stuck on.
    const onBlur = () => {
      heldRef.current = false;
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);

    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  return heldRef;
}
