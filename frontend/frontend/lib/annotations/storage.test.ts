import { beforeEach, describe, expect, it } from 'vitest';
import {
  STORAGE_VERSION,
  clearAnnotations,
  documentKeyFor,
  loadAnnotations,
  saveAnnotations
} from './storage';
import type { AnnotationState } from './types';

/** Minimal in-memory Storage so these tests need no DOM. */
class MemoryStorage {
  private entries = new Map<string, string>();

  get length(): number {
    return this.entries.size;
  }

  clear(): void {
    this.entries.clear();
  }

  getItem(key: string): string | null {
    return this.entries.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.entries.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }

  setItem(key: string, value: string): void {
    this.entries.set(key, value);
  }
}

const memory = new MemoryStorage();

beforeEach(() => {
  memory.clear();
  Object.defineProperty(globalThis, 'localStorage', {
    value: memory,
    configurable: true,
    writable: true
  });
});

function stateWith(documentKey: string | null): AnnotationState {
  return {
    documentKey,
    layers: [
      { id: 'l1', name: 'Kerb', color: '#2563EB', kind: 'line', visible: true, shapes: [] }
    ],
    activeLayerId: 'l1',
    selectedShapeId: null,
    calibration: { pointsPerMillimetre: 0.4 }
  };
}

describe('documentKeyFor', () => {
  it('combines name, size, and page count', () => {
    expect(documentKeyFor({ name: 'site-plan.pdf', size: 20481 }, 65)).toBe(
      'site-plan.pdf:20481:65'
    );
  });

  it('distinguishes two files with the same name but different sizes', () => {
    const a = documentKeyFor({ name: 'plan.pdf', size: 100 }, 2);
    const b = documentKeyFor({ name: 'plan.pdf', size: 200 }, 2);
    expect(a).not.toBe(b);
  });
});

describe('saveAnnotations and loadAnnotations', () => {
  it('round-trips a state', () => {
    saveAnnotations(stateWith('plan.pdf:100:2'));
    expect(loadAnnotations('plan.pdf:100:2')).toEqual(stateWith('plan.pdf:100:2'));
  });

  it('returns null for a key that was never saved', () => {
    expect(loadAnnotations('missing')).toBeNull();
  });

  it('does nothing when the state has no document key', () => {
    saveAnnotations(stateWith(null));
    expect(memory.length).toBe(0);
  });

  it('keys entries per document', () => {
    saveAnnotations(stateWith('a.pdf:1:1'));
    saveAnnotations(stateWith('b.pdf:2:1'));

    expect(loadAnnotations('a.pdf:1:1')).not.toBeNull();
    expect(loadAnnotations('b.pdf:2:1')).not.toBeNull();
    expect(memory.length).toBe(2);
  });

  it('returns null for unparseable stored data', () => {
    memory.setItem('takeoff:annotations:broken', '{not json');
    expect(loadAnnotations('broken')).toBeNull();
  });

  it('returns null when the stored version does not match', () => {
    memory.setItem(
      'takeoff:annotations:old',
      JSON.stringify({ version: STORAGE_VERSION + 1, state: stateWith('old') })
    );
    expect(loadAnnotations('old')).toBeNull();
  });

  it('returns null when the payload lacks a layers array', () => {
    memory.setItem(
      'takeoff:annotations:bad',
      JSON.stringify({ version: STORAGE_VERSION, state: { documentKey: 'bad' } })
    );
    expect(loadAnnotations('bad')).toBeNull();
  });
});

describe('clearAnnotations', () => {
  it('removes only the named document', () => {
    saveAnnotations(stateWith('a.pdf:1:1'));
    saveAnnotations(stateWith('b.pdf:2:1'));

    clearAnnotations('a.pdf:1:1');

    expect(loadAnnotations('a.pdf:1:1')).toBeNull();
    expect(loadAnnotations('b.pdf:2:1')).not.toBeNull();
  });
});

describe('missing localStorage', () => {
  it('degrades to a no-op rather than throwing', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      value: undefined,
      configurable: true,
      writable: true
    });

    expect(() => saveAnnotations(stateWith('a.pdf:1:1'))).not.toThrow();
    expect(loadAnnotations('a.pdf:1:1')).toBeNull();
    expect(() => clearAnnotations('a.pdf:1:1')).not.toThrow();
  });
});
