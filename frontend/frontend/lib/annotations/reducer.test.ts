import { describe, expect, it } from 'vitest';
import { annotationReducer, initialAnnotationState } from './reducer';
import type { AnnotationAction } from './reducer';
import type { AnnotationState, Shape } from './types';
import { LAYER_PALETTE } from './types';

function reduce(state: AnnotationState, ...actions: AnnotationAction[]): AnnotationState {
  return actions.reduce(annotationReducer, state);
}

function shape(id: string, kind: 'line' | 'area' = 'line', pageNumber = 1): Shape {
  return {
    id,
    kind,
    pageNumber,
    points: [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 }
    ]
  };
}

describe('initialAnnotationState', () => {
  it('starts empty', () => {
    expect(initialAnnotationState).toEqual({
      documentKey: null,
      layers: [],
      activeLayerId: null,
      selectedShapeId: null,
      calibration: null
    });
  });
});

describe('addLayer', () => {
  it('creates a named, coloured, active layer', () => {
    const state = reduce(initialAnnotationState, {
      type: 'addLayer',
      id: 'l1',
      kind: 'line'
    });

    expect(state.layers).toHaveLength(1);
    expect(state.layers[0]).toEqual({
      id: 'l1',
      name: 'Layer 1',
      color: LAYER_PALETTE[0],
      kind: 'line',
      visible: true,
      shapes: []
    });
    expect(state.activeLayerId).toBe('l1');
  });

  it('walks the palette and increments the name', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addLayer', id: 'l2', kind: 'area' }
    );

    expect(state.layers[1]!.name).toBe('Layer 2');
    expect(state.layers[1]!.color).toBe(LAYER_PALETTE[1]);
    expect(state.activeLayerId).toBe('l2');
  });

  it('wraps the palette once it runs out', () => {
    let state = initialAnnotationState;
    for (let index = 0; index < LAYER_PALETTE.length + 1; index += 1) {
      state = annotationReducer(state, { type: 'addLayer', id: `l${index}`, kind: 'line' });
    }

    expect(state.layers[LAYER_PALETTE.length]!.color).toBe(LAYER_PALETTE[0]);
  });
});

describe('renameLayer, setLayerColor, toggleLayerVisibility', () => {
  const base = reduce(initialAnnotationState, { type: 'addLayer', id: 'l1', kind: 'line' });

  it('renames a layer', () => {
    const state = annotationReducer(base, {
      type: 'renameLayer',
      layerId: 'l1',
      name: 'Kerb line'
    });
    expect(state.layers[0]!.name).toBe('Kerb line');
  });

  it('recolours a layer', () => {
    const state = annotationReducer(base, {
      type: 'setLayerColor',
      layerId: 'l1',
      color: '#123456'
    });
    expect(state.layers[0]!.color).toBe('#123456');
  });

  it('toggles visibility off and back on', () => {
    const hidden = annotationReducer(base, { type: 'toggleLayerVisibility', layerId: 'l1' });
    expect(hidden.layers[0]!.visible).toBe(false);

    const shown = annotationReducer(hidden, { type: 'toggleLayerVisibility', layerId: 'l1' });
    expect(shown.layers[0]!.visible).toBe(true);
  });

  it('ignores an unknown layer id', () => {
    const state = annotationReducer(base, {
      type: 'renameLayer',
      layerId: 'nope',
      name: 'x'
    });
    expect(state.layers[0]!.name).toBe('Layer 1');
  });
});

describe('addShape', () => {
  it('appends to the active layer when the kinds match', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'unused' }
    );

    expect(state.layers).toHaveLength(1);
    expect(state.layers[0]!.shapes.map((s) => s.id)).toEqual(['s1']);
  });

  it('creates a layer when none is active', () => {
    const state = annotationReducer(initialAnnotationState, {
      type: 'addShape',
      shape: shape('s1'),
      fallbackLayerId: 'l-new'
    });

    expect(state.layers).toHaveLength(1);
    expect(state.layers[0]!.id).toBe('l-new');
    expect(state.layers[0]!.kind).toBe('line');
    expect(state.layers[0]!.shapes).toHaveLength(1);
    expect(state.activeLayerId).toBe('l-new');
  });

  it('creates a layer when the active layer holds the other kind', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1', 'area'), fallbackLayerId: 'l-area' }
    );

    expect(state.layers).toHaveLength(2);
    expect(state.layers[1]!.id).toBe('l-area');
    expect(state.layers[1]!.kind).toBe('area');
    expect(state.layers[0]!.shapes).toHaveLength(0);
    expect(state.activeLayerId).toBe('l-area');
  });
});

describe('deleteShape', () => {
  it('removes the shape from whichever layer holds it', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'addShape', shape: shape('s2'), fallbackLayerId: 'x' },
      { type: 'deleteShape', shapeId: 's1' }
    );

    expect(state.layers[0]!.shapes.map((s) => s.id)).toEqual(['s2']);
  });

  it('clears the selection when the selected shape is removed', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'selectShape', shapeId: 's1' },
      { type: 'deleteShape', shapeId: 's1' }
    );

    expect(state.selectedShapeId).toBeNull();
  });

  it('leaves a different selection alone', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'addShape', shape: shape('s2'), fallbackLayerId: 'x' },
      { type: 'selectShape', shapeId: 's2' },
      { type: 'deleteShape', shapeId: 's1' }
    );

    expect(state.selectedShapeId).toBe('s2');
  });
});

describe('deleteLayer', () => {
  it('promotes the first remaining layer to active', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addLayer', id: 'l2', kind: 'line' },
      { type: 'deleteLayer', layerId: 'l2' }
    );

    expect(state.layers.map((l) => l.id)).toEqual(['l1']);
    expect(state.activeLayerId).toBe('l1');
  });

  it('clears the active layer when the last one goes', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'deleteLayer', layerId: 'l1' }
    );

    expect(state.layers).toEqual([]);
    expect(state.activeLayerId).toBeNull();
  });

  it('clears the selection when the deleted layer held the selected shape', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'selectShape', shapeId: 's1' },
      { type: 'addLayer', id: 'l2', kind: 'line' },
      { type: 'deleteLayer', layerId: 'l1' }
    );

    expect(state.selectedShapeId).toBeNull();
    expect(state.activeLayerId).toBe('l2');
  });

  it('keeps a selection that lived in a surviving layer', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'l1', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'addLayer', id: 'l2', kind: 'line' },
      { type: 'selectShape', shapeId: 's1' },
      { type: 'deleteLayer', layerId: 'l2' }
    );

    expect(state.selectedShapeId).toBe('s1');
  });
});

describe('setActiveLayer, selectShape, setCalibration, reset', () => {
  const base = reduce(
    initialAnnotationState,
    { type: 'addLayer', id: 'l1', kind: 'line' },
    { type: 'addLayer', id: 'l2', kind: 'area' }
  );

  it('sets the active layer', () => {
    expect(annotationReducer(base, { type: 'setActiveLayer', layerId: 'l1' }).activeLayerId).toBe(
      'l1'
    );
  });

  it('stores and clears a calibration', () => {
    const calibrated = annotationReducer(base, {
      type: 'setCalibration',
      calibration: { pointsPerMillimetre: 0.5 }
    });
    expect(calibrated.calibration).toEqual({ pointsPerMillimetre: 0.5 });

    const cleared = annotationReducer(calibrated, { type: 'setCalibration', calibration: null });
    expect(cleared.calibration).toBeNull();
  });

  it('resets everything but keeps the document key', () => {
    const withKey = annotationReducer(base, {
      type: 'loadDocument',
      documentKey: 'plan.pdf:1000:6',
      restored: null
    });
    const state = annotationReducer(
      annotationReducer(withKey, { type: 'addLayer', id: 'l9', kind: 'line' }),
      { type: 'reset' }
    );

    expect(state.layers).toEqual([]);
    expect(state.documentKey).toBe('plan.pdf:1000:6');
  });
});

describe('importExtraction', () => {
  function extractedLayer(id: string, shapeIds: string[]): AnnotationState['layers'][number] {
    return {
      id,
      name: id,
      color: '#2563EB',
      kind: 'line',
      visible: true,
      source: 'extraction',
      shapes: shapeIds.map((shapeId) => ({
        id: shapeId,
        kind: 'line' as const,
        pageNumber: 1,
        points: [
          { x: 0, y: 0 },
          { x: 10, y: 0 }
        ]
      }))
    };
  }

  it('adds the extracted layers', () => {
    const state = annotationReducer(initialAnnotationState, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001'])]
    });

    expect(state.layers).toHaveLength(1);
    expect(state.layers[0]!.source).toBe('extraction');
  });

  it('keeps the user’s own layers', () => {
    const withUserWork = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'mine', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' }
    );

    const state = annotationReducer(withUserWork, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001'])]
    });

    expect(state.layers.map((l) => l.id)).toContain('mine');
    expect(state.layers.find((l) => l.id === 'mine')!.shapes).toHaveLength(1);
  });

  it('replaces a previous extraction instead of stacking two generations', () => {
    const first = annotationReducer(initialAnnotationState, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001', '1:w-002'])]
    });

    const second = annotationReducer(first, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001'])]
    });

    expect(second.layers).toHaveLength(1);
    expect(second.layers[0]!.shapes).toHaveLength(1);
  });

  it('clears a selection that pointed into the replaced extraction', () => {
    const imported = reduce(
      initialAnnotationState,
      { type: 'importExtraction', layers: [extractedLayer('extraction:walls', ['1:w-009'])] },
      { type: 'selectShape', shapeId: '1:w-009' }
    );

    const reimported = annotationReducer(imported, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001'])]
    });

    expect(reimported.selectedShapeId).toBeNull();
  });

  it('keeps a selection that points at the user’s own shape', () => {
    const state = reduce(
      initialAnnotationState,
      { type: 'addLayer', id: 'mine', kind: 'line' },
      { type: 'addShape', shape: shape('s1'), fallbackLayerId: 'x' },
      { type: 'selectShape', shapeId: 's1' },
      { type: 'importExtraction', layers: [extractedLayer('extraction:walls', ['1:w-001'])] }
    );

    expect(state.selectedShapeId).toBe('s1');
  });

  it('adopts a calibration when one is supplied', () => {
    const state = annotationReducer(initialAnnotationState, {
      type: 'importExtraction',
      layers: [],
      calibration: { pointsPerMillimetre: 0.02835 }
    });

    expect(state.calibration).toEqual({ pointsPerMillimetre: 0.02835 });
  });

  it('leaves an existing calibration alone when none is supplied', () => {
    const calibrated = annotationReducer(initialAnnotationState, {
      type: 'setCalibration',
      calibration: { pointsPerMillimetre: 0.5 }
    });

    const state = annotationReducer(calibrated, {
      type: 'importExtraction',
      layers: [extractedLayer('extraction:walls', ['1:w-001'])]
    });

    expect(state.calibration).toEqual({ pointsPerMillimetre: 0.5 });
  });

  it('does not overwrite a calibration the user set with a null', () => {
    const calibrated = annotationReducer(initialAnnotationState, {
      type: 'setCalibration',
      calibration: { pointsPerMillimetre: 0.5 }
    });

    const state = annotationReducer(calibrated, {
      type: 'importExtraction',
      layers: [],
      calibration: null
    });

    expect(state.calibration).toEqual({ pointsPerMillimetre: 0.5 });
  });
});

describe('loadDocument', () => {
  it('starts clean when there is nothing to restore', () => {
    const dirty = reduce(initialAnnotationState, { type: 'addLayer', id: 'l1', kind: 'line' });
    const state = annotationReducer(dirty, {
      type: 'loadDocument',
      documentKey: 'a.pdf:10:2',
      restored: null
    });

    expect(state).toEqual({
      documentKey: 'a.pdf:10:2',
      layers: [],
      activeLayerId: null,
      selectedShapeId: null,
      calibration: null
    });
  });

  it('adopts restored state but trusts the action for the document key', () => {
    const restored: AnnotationState = {
      documentKey: 'stale-key',
      layers: [
        { id: 'l1', name: 'Kerb', color: '#2563EB', kind: 'line', visible: true, shapes: [] }
      ],
      activeLayerId: 'l1',
      selectedShapeId: null,
      calibration: { pointsPerMillimetre: 0.25 }
    };

    const state = annotationReducer(initialAnnotationState, {
      type: 'loadDocument',
      documentKey: 'fresh-key',
      restored
    });

    expect(state.documentKey).toBe('fresh-key');
    expect(state.layers).toHaveLength(1);
    expect(state.calibration).toEqual({ pointsPerMillimetre: 0.25 });
  });
});
