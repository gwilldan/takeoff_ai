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
import { historyReducer, startHistory, type HistoryAction, type Version } from './history';
import { initialAnnotationState } from './reducer';
import { saveAnnotations } from './storage';
import type { AnnotationState } from './types';

const SAVE_DEBOUNCE_MS = 400;

type AnnotationContextValue = {
  state: AnnotationState;
  dispatch: Dispatch<HistoryAction>;
  /** Earlier versions, oldest first, and how many edits redo can bring back. */
  versions: Version[];
  current: { label: string; at: number };
  redoCount: number;
};

const AnnotationContext = createContext<AnnotationContextValue | null>(null);

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(element && (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.isContentEditable));
}

export function AnnotationProvider({ children }: { children: ReactNode }) {
  const [history, dispatch] = useReducer(historyReducer, initialAnnotationState, startHistory);
  const state = history.present;

  // Persist on a trailing debounce: drawing a polyline fires several updates
  // in a row and only the last one needs to reach localStorage.
  useEffect(() => {
    if (!state.documentKey) {
      return;
    }

    const timer = window.setTimeout(() => saveAnnotations(state), SAVE_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [state]);

  // Undo: ⌘Z on macOS, Ctrl+Z elsewhere. Redo: ⇧⌘Z / Ctrl+Shift+Z, or Ctrl+Y.
  // Either modifier is accepted on every platform; text fields keep their own undo.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || isTyping(event.target)) {
        return;
      }

      const key = event.key.toLowerCase();
      if (key === 'z' || key === 'y') {
        event.preventDefault();
        dispatch({ type: key === 'y' || event.shiftKey ? 'redo' : 'undo' });
      }
    };

    window.addEventListener('keydown', onKeyDown);

    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const value = useMemo(
    () => ({
      state,
      dispatch,
      versions: history.past,
      current: { label: history.label, at: history.at },
      redoCount: history.future.length
    }),
    [history, state]
  );

  return <AnnotationContext.Provider value={value}>{children}</AnnotationContext.Provider>;
}

export function useAnnotations(): AnnotationContextValue {
  const value = useContext(AnnotationContext);

  if (!value) {
    throw new Error('useAnnotations must be used inside an AnnotationProvider');
  }

  return value;
}
