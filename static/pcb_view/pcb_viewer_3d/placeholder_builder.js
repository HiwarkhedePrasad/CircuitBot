/**
 * placeholder_builder.js — Procedural placeholder 3D models for components without real STEP/VRML data.
 */
class PlaceholderBuilder {
    /**
     * Build a placeholder mesh for a component based on its category.
     * Returns a THREE.Group with geometry and material.
     */
    static build(component) {
        const cat = PlaceholderBuilder._categorize(component);
        const group = new THREE.Group();
        group.name = `placeholder_${component.ref || 'unknown'}`;

        let bodyGeo, bodyMat;

        switch (cat) {
            case 'resistor':
                bodyGeo = new THREE.BoxGeometry(1.6, 0.3, 0.8);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.RESISTOR, roughness: 0.5 });
                break;
            case 'capacitor':
                bodyGeo = new THREE.BoxGeometry(1.6, 0.5, 0.8);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.CAPACITOR, roughness: 0.6 });
                break;
            case 'ic':
                bodyGeo = new THREE.BoxGeometry(3.9, 0.3, 4.9);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.IC, roughness: 0.4 });
                break;
            case 'connector':
                bodyGeo = new THREE.BoxGeometry(5, 3, 5);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.CONNECTOR, roughness: 0.5 });
                break;
            case 'led':
                bodyGeo = new THREE.CylinderGeometry(0.4, 0.4, 0.5, 8);
                bodyMat = new THREE.MeshStandardMaterial({
                    color: PCB3D.COMP_COLORS.LED,
                    roughness: 0.3,
                    emissive: PCB3D.COMP_COLORS.LED,
                    emissiveIntensity: 0.2,
                });
                break;
            case 'crystal':
                bodyGeo = new THREE.BoxGeometry(3.2, 1.0, 1.5);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.CRYSTAL, roughness: 0.2, metalness: 0.8 });
                break;
            case 'inductor':
                bodyGeo = new THREE.CylinderGeometry(1.0, 1.0, 1.0, 12);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.INDUCTOR, roughness: 0.6 });
                break;
            case 'diode':
                bodyGeo = new THREE.BoxGeometry(1.6, 0.4, 0.8);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.DIODE, roughness: 0.5 });
                break;
            default:
                bodyGeo = new THREE.BoxGeometry(2, 0.5, 2);
                bodyMat = new THREE.MeshStandardMaterial({ color: PCB3D.COMP_COLORS.GENERIC, roughness: 0.5 });
        }

        const body = new THREE.Mesh(bodyGeo, bodyMat);
        body.position.y = 0.3; // Slightly above board surface
        group.add(body);

        // Add pin stubs for through-hole components
        if (PlaceholderBuilder._isTHT(component)) {
            const pinMat = new THREE.MeshStandardMaterial({ color: 0xcccccc, metalness: 0.6, roughness: 0.3 });
            for (const pad of (component.pads || [])) {
                if (pad.type === 'tht' || pad.drill) {
                    const pinGeo = new THREE.CylinderGeometry(0.15, 0.15, 1.5, 6);
                    const pin = new THREE.Mesh(pinGeo, pinMat);
                    pin.position.set(pad.x || 0, -0.3, -(pad.y || 0));
                    group.add(pin);
                }
            }
        }

        return group;
    }

    static _categorize(component) {
        const fp = (component.footprint || '').toLowerCase();
        const ref = (component.ref || '').toUpperCase();

        if (fp.includes('resistor') || ref.startsWith('R')) return 'resistor';
        if (fp.includes('capacitor') || ref.startsWith('C')) return 'capacitor';
        if (fp.includes('soic') || fp.includes('qfp') || fp.includes('qfn') || fp.includes('dfn') || fp.includes('dip') || fp.includes('tssop') || ref.startsWith('U')) return 'ic';
        if (fp.includes('connector') || fp.includes('header') || fp.includes('socket') || fp.includes('usb') || fp.includes('barrel')) return 'connector';
        if (fp.includes('led') || ref.startsWith('D')) return 'led';
        if (fp.includes('crystal') || fp.includes('oscillator') || ref.startsWith('Y') || ref.startsWith('X')) return 'crystal';
        if (fp.includes('inductor') || ref.startsWith('L')) return 'inductor';
        if (fp.includes('diode') || fp.includes('sot-23')) return 'diode';
        return 'generic';
    }

    static _isTHT(component) {
        return (component.pads || []).some(p => p.type === 'tht' || p.drill);
    }
}
