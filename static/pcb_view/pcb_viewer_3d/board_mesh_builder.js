/**
 * board_mesh_builder.js — Builds 3D meshes for PCB substrate, copper, solder mask, silkscreen.
 *
 * Coordinate mapping: Three.js X = KiCad X, Three.js Z = -KiCad Y, Three.js Y = height.
 */
class BoardMeshBuilder {
    constructor() {
        this.group = new THREE.Group();
        this.group.name = 'board';
    }

    /**
     * Build the complete board from a BoardModel dict.
     */
    build(boardModel) {
        this.group.clear();
        if (!boardModel) return this.group;

        const outline = boardModel.outline_segments || [];
        const components = boardModel.components || [];
        const traces = boardModel.traces || [];
        const vias = boardModel.vias || [];

        // Compute board bounding box from outline or components/traces fallback
        const bbox = this._computeBBox(outline, boardModel);
        const boardW = bbox.maxX - bbox.minX;
        const boardH = bbox.maxY - bbox.minY;
        const thickness = PCB3D.DEFAULT_THICKNESS;

        // Center offset so board is at origin
        const cx = (bbox.minX + bbox.maxX) / 2;
        const cz = -(bbox.minY + bbox.maxY) / 2;
        this.centerOffset = { cx, cz };

        // 1. Substrate
        this._buildSubstrate(bbox, thickness);

        // 2. Copper layers
        this._buildCopperTraces(traces, cx, cz);
        this._buildVias(vias, cx, cz);
        this._buildComponentPads(components, cx, cz);

        return this.group;
    }

    _computeBBox(outlineSegments, boardModel) {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const seg of (outlineSegments || [])) {
            if (seg.type === 'line' || seg.type === 'segment' || seg.points) {
                for (const pt of (seg.points || [])) {
                    minX = Math.min(minX, pt.x); maxX = Math.max(maxX, pt.x);
                    minY = Math.min(minY, pt.y); maxY = Math.max(maxY, pt.y);
                }
            }
        }
        // Fallback if outline has no points
        if (minX === Infinity && boardModel) {
            for (const comp of (boardModel.components || [])) {
                minX = Math.min(minX, comp.x || 0); maxX = Math.max(maxX, comp.x || 0);
                minY = Math.min(minY, comp.y || 0); maxY = Math.max(maxY, comp.y || 0);
            }
            for (const trace of (boardModel.traces || [])) {
                for (const pt of (trace.path || [])) {
                    minX = Math.min(minX, pt.x || 0); maxX = Math.max(maxX, pt.x || 0);
                    minY = Math.min(minY, pt.y || 0); maxY = Math.max(maxY, pt.y || 0);
                }
            }
        }
        if (minX === Infinity) return { minX: -50, minY: -50, maxX: 50, maxY: 50 };
        if (maxX === minX) { minX -= 10; maxX += 10; }
        if (maxY === minY) { minY -= 10; maxY += 10; }
        return { minX, minY, maxX, maxY };
    }

    _buildSubstrate(bbox, thickness) {
        if (!bbox) return;
        const w = (bbox.maxX - bbox.minX);
        const h = (bbox.maxY - bbox.minY);
        const shape = new THREE.Shape();
        shape.moveTo(-w / 2, -h / 2);
        shape.lineTo(w / 2, -h / 2);
        shape.lineTo(w / 2, h / 2);
        shape.lineTo(-w / 2, h / 2);
        shape.lineTo(-w / 2, -h / 2);

        const extrudeSettings = {
            depth: thickness,
            bevelEnabled: false,
        };
        const geo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
        geo.center();
        const mat = new THREE.MeshStandardMaterial({
            color: PCB3D.COLORS.SUBSTRATE,
            transparent: true,
            opacity: 0.85,
            roughness: 0.6,
            metalness: 0.1,
            side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.rotation.x = -Math.PI / 2; // Lay flat on XZ plane
        mesh.name = 'substrate';
        this.group.add(mesh);
    }

    _buildCopperTraces(traces, cx, cz) {
        const topGroup = new THREE.Group();
        topGroup.name = 'copper_fcu';
        const botGroup = new THREE.Group();
        botGroup.name = 'copper_bcu';

        const traceMat = new THREE.MeshStandardMaterial({
            color: PCB3D.COLORS.COPPER,
            roughness: 0.4,
            metalness: 0.7,
        });

        for (const trace of traces) {
            if (!trace.path || trace.path.length < 2) continue;
            const isBottom = trace.layer === 'B.Cu';
            const yOffset = isBottom ? PCB3D.LAYER_Y.B_CU : PCB3D.LAYER_Y.F_CU;

            // Create a thin extruded shape along the trace path
            const points = trace.path.map(p => new THREE.Vector3(
                p.x - cx, yOffset, -(p.y) - cz
            ));
            const curve = new THREE.CatmullRomCurve3(points, false, 'centripetal', 0.0);
            const tubeGeo = new THREE.TubeGeometry(curve, Math.max(points.length * 2, 4), (trace.width || 0.25) / 2, 4, false);
            const mesh = new THREE.Mesh(tubeGeo, traceMat);
            mesh.name = `trace_${trace.net || ''}`;
            (isBottom ? botGroup : topGroup).add(mesh);
        }

        this.group.add(topGroup);
        this.group.add(botGroup);
    }

    _buildVias(vias, cx, cz) {
        const viaGroup = new THREE.Group();
        viaGroup.name = 'vias';

        const viaMat = new THREE.MeshStandardMaterial({
            color: PCB3D.COLORS.VIA,
            roughness: 0.4,
            metalness: 0.6,
        });

        for (const via of vias) {
            const radius = (via.diameter || 0.6) / 2;
            const geo = new THREE.CylinderGeometry(radius, radius, 0.1, 12);
            const mesh = new THREE.Mesh(geo, viaMat);
            mesh.position.set(via.x - cx, 0, -(via.y) - cz);
            mesh.name = `via_${via.net || ''}`;
            viaGroup.add(mesh);

            // Drill hole
            const drillGeo = new THREE.CylinderGeometry(
                (via.drill || 0.3) / 2, (via.drill || 0.3) / 2, 0.15, 8
            );
            const drillMat = new THREE.MeshStandardMaterial({ color: PCB3D.COLORS.DRILL });
            const drillMesh = new THREE.Mesh(drillGeo, drillMat);
            drillMesh.position.copy(mesh.position);
            viaGroup.add(drillMesh);
        }

        this.group.add(viaGroup);
    }

    _buildComponentPads(components, cx, cz) {
        const padGroup = new THREE.Group();
        padGroup.name = 'pads';

        const padMat = new THREE.MeshStandardMaterial({
            color: PCB3D.COLORS.PAD,
            roughness: 0.3,
            metalness: 0.7,
        });

        for (const comp of components) {
            if (!comp.pads) continue;
            const isBottom = comp.layer === 'B.Cu';
            const yBase = isBottom ? PCB3D.LAYER_Y.B_CU : PCB3D.LAYER_Y.F_CU;

            for (const pad of comp.pads) {
                const px = comp.x + (pad.x || 0) - cx;
                const pz = -(comp.y + (pad.y || 0)) - cz;
                const pw = pad.width || 0.5;
                const ph = pad.height || 0.5;

                let geo;
                if (pad.shape === 'circle') {
                    geo = new THREE.CylinderGeometry(pw / 2, pw / 2, 0.04, 12);
                } else {
                    geo = new THREE.BoxGeometry(pw, 0.04, ph);
                }
                const mesh = new THREE.Mesh(geo, padMat);
                mesh.position.set(px, yBase, pz);
                if (pad.rotation) {
                    mesh.rotation.y = -pad.rotation * Math.PI / 180;
                }
                padGroup.add(mesh);
            }
        }

        this.group.add(padGroup);
    }

    dispose() {
        this.group.traverse(child => {
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(m => m.dispose());
                } else {
                    child.material.dispose();
                }
            }
        });
        this.group.clear();
    }
}
