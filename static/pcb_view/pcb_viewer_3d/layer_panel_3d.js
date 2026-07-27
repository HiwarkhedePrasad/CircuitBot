/**
 * layer_panel_3d.js — 3D layer visibility toggles and opacity controls.
 */
class LayerPanel3D {
    constructor(boardGroup) {
        this.boardGroup = boardGroup;
        this._layers = new Map();
        this._initLayers();
    }

    _initLayers() {
        const layerDefs = [
            { name: 'Substrate',  group: 'substrate',       visible: true, opacity: 0.85 },
            { name: 'F.Cu',       group: 'copper_fcu',      visible: true, opacity: 1.0 },
            { name: 'B.Cu',       group: 'copper_bcu',      visible: true, opacity: 1.0 },
            { name: 'Pads',       group: 'pads',            visible: true, opacity: 1.0 },
            { name: 'Vias',       group: 'vias',            visible: true, opacity: 1.0 },
            { name: 'Components', group: 'components',      visible: true, opacity: 1.0 },
        ];

        for (const def of layerDefs) {
            this._layers.set(def.name, { ...def });
        }
    }

    setLayerVisible(name, visible) {
        const layer = this._layers.get(name);
        if (!layer) return;
        layer.visible = visible;
        this._applyVisibility(name);
    }

    setLayerOpacity(name, opacity) {
        const layer = this._layers.get(name);
        if (!layer) return;
        layer.opacity = opacity;
        this._applyOpacity(name);
    }

    toggleLayer(name) {
        const layer = this._layers.get(name);
        if (!layer) return;
        layer.visible = !layer.visible;
        this._applyVisibility(name);
        return layer.visible;
    }

    _applyVisibility(name) {
        const layer = this._layers.get(name);
        if (!layer) return;
        const obj = this.boardGroup.getObjectByName(layer.group);
        if (obj) obj.visible = layer.visible;
    }

    _applyOpacity(name) {
        const layer = this._layers.get(name);
        if (!layer) return;
        const obj = this.boardGroup.getObjectByName(layer.group);
        if (!obj) return;
        obj.traverse(child => {
            if (child.isMesh && child.material) {
                child.material.transparent = layer.opacity < 1.0;
                child.material.opacity = layer.opacity;
                child.material.needsUpdate = true;
            }
        });
    }

    getLayerState(name) {
        return this._layers.get(name) || null;
    }

    getAllLayers() {
        return Array.from(this._layers.values());
    }
}
