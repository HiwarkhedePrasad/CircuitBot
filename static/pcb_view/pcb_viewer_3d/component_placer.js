/**
 * component_placer.js — Places 3D models at correct board positions.
 */
class ComponentPlacer {
    constructor(boardGroup) {
        this.boardGroup = boardGroup;
        this.componentGroup = new THREE.Group();
        this.componentGroup.name = 'components';
        this.boardGroup.add(this.componentGroup);
        this._componentMap = new Map(); // ref → THREE.Group
    }

    clear() {
        for (const [, wrapper] of this._componentMap) {
            this.componentGroup.remove(wrapper);
        }
        this._componentMap.clear();
    }

    /**
     * Place a component's 3D model at its board position.
     */
    place(component, modelGroup, centerOffset) {
        const isBottom = component.layer === 'B.Cu';
        const yBase = isBottom ? PCB3D.LAYER_Y.B_CU + 0.05 : PCB3D.LAYER_Y.F_CU + 0.05;

        const wrapper = new THREE.Group();
        wrapper.name = component.ref || 'unknown';
        wrapper.userData = { ref: component.ref, footprint: component.footprint, _originalY: yBase };

        if (modelGroup) {
            wrapper.add(modelGroup);
        }

        // Position on board (KiCad X → Three.js X, KiCad Y → Three.js -Z) minus center offset
        const cx = centerOffset ? centerOffset.cx : 0;
        const cz = centerOffset ? centerOffset.cz : 0;
        wrapper.position.set((component.x || 0) - cx, yBase, -(component.y || 0) - cz);

        // Rotation (KiCad degrees → radians, Y-axis rotation since components lie flat)
        if (component.rotation) {
            wrapper.rotation.y = -(component.rotation * Math.PI / 180);
        }

        // Flip bottom-side components
        if (isBottom) {
            wrapper.scale.z = -1;
            wrapper.rotation.x = Math.PI;
        }

        this.componentGroup.add(wrapper);
        this._componentMap.set(component.ref, wrapper);
        return wrapper;
    }

    /**
     * Place a placeholder for a component.
     */
    placePlaceholder(component, centerOffset) {
        const placeholder = PlaceholderBuilder.build(component);
        return this.place(component, placeholder, centerOffset);
    }

    /**
     * Get a placed component group by reference designator.
     */
    getComponent(ref) {
        return this._componentMap.get(ref) || null;
    }

    /**
     * Highlight a component (change material emissive).
     */
    highlight(ref, color = 0x4488ff) {
        const comp = this._componentMap.get(ref);
        if (!comp) return;
        comp.traverse(child => {
            if (child.isMesh && child.material) {
                child.material.emissive = new THREE.Color(color);
                child.material.emissiveIntensity = 0.3;
            }
        });
    }

    /**
     * Remove highlight from a component.
     */
    unhighlight(ref) {
        const comp = this._componentMap.get(ref);
        if (!comp) return;
        comp.traverse(child => {
            if (child.isMesh && child.material) {
                child.material.emissive = new THREE.Color(0x000000);
                child.material.emissiveIntensity = 0;
            }
        });
    }

    /**
     * Explode view: offset components vertically from the board.
     */
    explode(factor = 3.0) {
        for (const [ref, comp] of this._componentMap) {
            const isBottom = comp.position.y < 0;
            const sign = isBottom ? -1 : 1;
            comp.position.y += sign * factor;
        }
    }

    /**
     * Collapse back from exploded view.
     */
    collapse() {
        // Re-place all components at their correct positions
        // This requires re-reading the original positions, so we store them
        for (const [ref, comp] of this._componentMap) {
            // Reset to original Y (stored in userData)
            if (comp.userData._originalY !== undefined) {
                comp.position.y = comp.userData._originalY;
            }
        }
    }

    dispose() {
        this._componentMap.clear();
        this.componentGroup.traverse(child => {
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(m => m.dispose());
                } else {
                    child.material.dispose();
                }
            }
        });
        this.componentGroup.clear();
    }
}
