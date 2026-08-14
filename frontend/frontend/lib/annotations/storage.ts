import type { AnnotationState } from './types';

export const STORAGE_VERSION = 1;

const KEY_PREFIX = 'takeoff:annotations:';

type StoredPayload = {
  version: number;
  state: AnnotationState;
};

/**
 * Returns localStorage when it is usable, otherwise null. Server rendering has
 * no localStorage at all, and Safari's private mode can throw on access.
 */
function readStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/**
 * A stable identity for an opened document. Files are read in the browser and
 * never uploaded for annotation purposes, so name, byte size, and page count
 * are all that is available — enough to recognise the same file reopened.
 */
export function documentKeyFor(file: { name: string; size: number }, pageCount: number): string {
  return `${file.name}:${file.size}:${pageCount}`;
}

function isAnnotationState(value: unknown): value is AnnotationState {
  if (typeof value !== 'object' || value === null) {
    return false;
  }

  return Array.isArray((value as { layers?: unknown }).layers);
}

export function loadAnnotations(documentKey: string): AnnotationState | null {
  const storage = readStorage();

  if (!storage) {
    return null;
  }

  try {
    const raw = storage.getItem(`${KEY_PREFIX}${documentKey}`);

    if (!raw) {
      return null;
    }

    const payload = JSON.parse(raw) as StoredPayload;

    if (payload.version !== STORAGE_VERSION || !isAnnotationState(payload.state)) {
      return null;
    }

    return payload.state;
  } catch {
    return null;
  }
}

export function saveAnnotations(state: AnnotationState): void {
  const storage = readStorage();

  if (!storage || !state.documentKey) {
    return;
  }

  const payload: StoredPayload = { version: STORAGE_VERSION, state };

  try {
    storage.setItem(`${KEY_PREFIX}${state.documentKey}`, JSON.stringify(payload));
  } catch {
    // A full or disabled quota must not break annotating.
  }
}

export function clearAnnotations(documentKey: string): void {
  const storage = readStorage();

  if (!storage) {
    return;
  }

  try {
    storage.removeItem(`${KEY_PREFIX}${documentKey}`);
  } catch {
    // Nothing useful to do here.
  }
}
