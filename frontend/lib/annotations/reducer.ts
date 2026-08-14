import type { AnnotationState, Calibration, Layer, Shape, ShapeKind } from './types';
import { LAYER_PALETTE } from './types';

export type AnnotationAction =
  | { type: 'loadDocument'; documentKey: string; restored: AnnotationState | null }
  | { type: 'addLayer'; id: string; kind: ShapeKind }
  | { type: 'renameLayer'; layerId: string; name: string }
  | { type: 'setLayerColor'; layerId: string; color: string }
  | { type: 'toggleLayerVisibility'; layerId: string }
  | { type: 'deleteLayer'; layerId: string }
  | { type: 'setActiveLayer'; layerId: string }
  | { type: 'addShape'; shape: Shape; fallbackLayerId: string }
  | { type: 'deleteShape'; shapeId: string }
  | { type: 'selectShape'; shapeId: string | null }
  | { type: 'setCalibration'; calibration: Calibration | null }
  | {
      type: 'importExtraction';
      layers: Layer[];
      calibration?: Calibration | null;
    }
  | { type: 'reset' };

export const initialAnnotationState: AnnotationState = {
  documentKey: null,
  layers: [],
  activeLayerId: null,
  selectedShapeId: null,
  calibration: null
};

function createLayer(id: string, kind: ShapeKind, existingCount: number): Layer {
  return {
    id,
    name: `Layer ${existingCount + 1}`,
    color: LAYER_PALETTE[existingCount % LAYER_PALETTE.length]!,
    kind,
    visible: true,
    shapes: []
  };
}

function mapLayer(
  state: AnnotationState,
  layerId: string,
  update: (layer: Layer) => Layer
): AnnotationState {
  return {
    ...state,
    layers: state.layers.map((layer) => (layer.id === layerId ? update(layer) : layer))
  };
}

export function annotationReducer(
  state: AnnotationState,
  action: AnnotationAction
): AnnotationState {
  switch (action.type) {
    case 'loadDocument':
      return action.restored
        ? { ...action.restored, documentKey: action.documentKey }
        : { ...initialAnnotationState, documentKey: action.documentKey };

    case 'addLayer': {
      const layer = createLayer(action.id, action.kind, state.layers.length);
      return { ...state, layers: [...state.layers, layer], activeLayerId: layer.id };
    }

    case 'renameLayer':
      return mapLayer(state, action.layerId, (layer) => ({ ...layer, name: action.name }));

    case 'setLayerColor':
      return mapLayer(state, action.layerId, (layer) => ({ ...layer, color: action.color }));

    case 'toggleLayerVisibility':
      return mapLayer(state, action.layerId, (layer) => ({ ...layer, visible: !layer.visible }));

    case 'deleteLayer': {
      const removed = state.layers.find((layer) => layer.id === action.layerId);
      const layers = state.layers.filter((layer) => layer.id !== action.layerId);
      const heldSelection =
        state.selectedShapeId !== null &&
        (removed?.shapes.some((shape) => shape.id === state.selectedShapeId) ?? false);

      return {
        ...state,
        layers,
        activeLayerId:
          state.activeLayerId === action.layerId ? (layers[0]?.id ?? null) : state.activeLayerId,
        selectedShapeId: heldSelection ? null : state.selectedShapeId
      };
    }

    case 'setActiveLayer':
      return { ...state, activeLayerId: action.layerId };

    case 'addShape': {
      const active = state.layers.find((layer) => layer.id === state.activeLayerId);

      // A shape can only join a layer of its own kind. Anything else gets a
      // fresh layer so length and area totals never mix in one row.
      if (!active || active.kind !== action.shape.kind) {
        const layer = createLayer(action.fallbackLayerId, action.shape.kind, state.layers.length);

        return {
          ...state,
          layers: [...state.layers, { ...layer, shapes: [action.shape] }],
          activeLayerId: layer.id
        };
      }

      return mapLayer(state, active.id, (layer) => ({
        ...layer,
        shapes: [...layer.shapes, action.shape]
      }));
    }

    case 'deleteShape':
      return {
        ...state,
        layers: state.layers.map((layer) => ({
          ...layer,
          shapes: layer.shapes.filter((shape) => shape.id !== action.shapeId)
        })),
        selectedShapeId: state.selectedShapeId === action.shapeId ? null : state.selectedShapeId
      };

    case 'selectShape':
      return { ...state, selectedShapeId: action.shapeId };

    case 'setCalibration':
      return { ...state, calibration: action.calibration };

    case 'importExtraction': {
      // Extracted layers replace previous extracted layers and leave the user's
      // own alone. Re-running extraction must not discard hand-drawn work, and
      // must not leave two generations of agent output stacked on the sheet.
      const userLayers = state.layers.filter((layer) => layer.source !== 'extraction');
      const layers = [...action.layers, ...userLayers];
      const importedIds = new Set(action.layers.flatMap((l) => l.shapes.map((s) => s.id)));
      const survivingUserShape =
        state.selectedShapeId !== null &&
        userLayers.some((l) => l.shapes.some((s) => s.id === state.selectedShapeId));

      return {
        ...state,
        layers,
        activeLayerId:
          layers.find((layer) => layer.id === state.activeLayerId)?.id ??
          layers[0]?.id ??
          null,
        selectedShapeId:
          survivingUserShape || (state.selectedShapeId && importedIds.has(state.selectedShapeId))
            ? state.selectedShapeId
            : null,
        calibration:
          action.calibration !== undefined
            ? (action.calibration ?? state.calibration)
            : state.calibration
      };
    }

    case 'reset':
      return { ...initialAnnotationState, documentKey: state.documentKey };

    default:
      return state;
  }
}
