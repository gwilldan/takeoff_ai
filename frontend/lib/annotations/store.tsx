'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode
} from 'react';
import { annotationReducer, initialAnnotationState, type AnnotationAction } from './reducer';
import { saveAnnotations } from './storage';
import type { AnnotationState } from './types';

const SAVE_DEBOUNCE_MS = 400;

type AnnotationContextValue = {
  state: AnnotationState;
  dispatch: Dispatch<AnnotationAction>;
};

const AnnotationContext = createContext<AnnotationContextValue | null>(null);

export function AnnotationProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(annotationReducer, initialAnnotationState);

  // Persist on a trailing debounce: drawing a polyline fires several updates
  // in a row and only the last one needs to reach localStorage.
  useEffect(() => {
    if (!state.documentKey) {
      return;
    }

    const timer = window.setTimeout(() => saveAnnotations(state), SAVE_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [state]);

  const value = useMemo(() => ({ state, dispatch }), [state]);

  return <AnnotationContext.Provider value={value}>{children}</AnnotationContext.Provider>;
}

export function useAnnotations(): AnnotationContextValue {
  const value = useContext(AnnotationContext);

  if (!value) {
    throw new Error('useAnnotations must be used inside an AnnotationProvider');
  }

  return value;
}
