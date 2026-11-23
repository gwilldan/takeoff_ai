import { annotationReducer, type AnnotationAction } from './reducer';
import type { AnnotationState } from './types';

/** How many earlier versions are kept to undo or restore. */
export const HISTORY_LIMIT = 20;

export type Version = { state: AnnotationState; label: string; at: number };

export type History = {
  present: AnnotationState;
  /** Oldest first; the last entry is what one undo returns to. */
  past: Version[];
  /** Nearest first; what redo walks forward through. */
  future: Version[];
  /** Label of the edit that produced `present`. */
  label: string;
  at: number;
};

export type HistoryAction =
  | AnnotationAction
  | { type: 'undo' }
  | { type: 'redo' }
  /** Go back to `past[index]`; everything after it stays redoable. */
  | { type: 'restore'; index: number };

/** Edits worth a version, by label. Anything else (selection, visibility) changes the present only. */
const EDIT_LABELS: Partial<Record<AnnotationAction['type'], string>> = {
  addLayer: 'Added a layer',
  renameLayer: 'Renamed a layer',
  setLayerColor: 'Changed a layer colour',
  deleteLayer: 'Deleted a layer',
  deletePageShapes: 'Removed a layer from a page',
  addShape: 'Drew a shape',
  deleteShape: 'Deleted a shape',
  updateShapePoints: 'Edited a shape',
  setCalibration: 'Set the scale',
  importExtraction: 'Imported the extraction',
  reset: 'Cleared annotations'
};

export function startHistory(present: AnnotationState): History {
  return { present, past: [], future: [], label: 'Opened', at: Date.now() };
}

/** Keep the selection only while the shape it points at still exists. */
function withLiveSelection(state: AnnotationState, selectedShapeId: string | null): AnnotationState {
  const exists = state.layers.some((layer) => layer.shapes.some((shape) => shape.id === selectedShapeId));
  return { ...state, selectedShapeId: exists ? selectedShapeId : null };
}

function moveTo(history: History, index: number): History {
  const target = history.past[index];
  if (!target) {
    return history;
  }

  const current: Version = { state: history.present, label: history.label, at: history.at };
  return {
    present: withLiveSelection(target.state, history.present.selectedShapeId),
    past: history.past.slice(0, index),
    future: [...history.past.slice(index + 1), current, ...history.future],
    label: target.label,
    at: target.at
  };
}

export function historyReducer(history: History, action: HistoryAction): History {
  switch (action.type) {
    case 'undo':
      return moveTo(history, history.past.length - 1);

    case 'restore':
      return moveTo(history, action.index);

    case 'redo': {
      const [next, ...rest] = history.future;
      if (!next) {
        return history;
      }

      return {
        present: withLiveSelection(next.state, history.present.selectedShapeId),
        past: [...history.past, { state: history.present, label: history.label, at: history.at }].slice(-HISTORY_LIMIT),
        future: rest,
        label: next.label,
        at: next.at
      };
    }

    case 'loadDocument':
      return startHistory(annotationReducer(history.present, action));

    default: {
      const present = annotationReducer(history.present, action);
      const label = EDIT_LABELS[action.type];

      if (present === history.present) {
        return history;
      }

      if (!label) {
        return { ...history, present };
      }

      // Pages of one extraction land one at a time; they are one version.
      if (action.type === 'importExtraction' && history.label === label) {
        return { ...history, present, at: Date.now() };
      }

      return {
        present,
        past: [...history.past, { state: history.present, label: history.label, at: history.at }].slice(-HISTORY_LIMIT),
        future: [],
        label,
        at: Date.now()
      };
    }
  }
}
