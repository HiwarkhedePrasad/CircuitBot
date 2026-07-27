/**
 * camera_controller.js — OrbitControls with PCB-specific camera presets.
 */
class CameraController {
    constructor(camera, domElement) {
        this.camera = camera;
        this.controls = new THREE.OrbitControls(camera, domElement);

        // PCB-friendly defaults
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.controls.rotateSpeed = 0.6;
        this.controls.zoomSpeed = 1.2;
        this.controls.panSpeed = 0.8;
        this.controls.minPolarAngle = 0.05;
        this.controls.maxPolarAngle = Math.PI - 0.05;
        this.controls.minDistance = 2;
        this.controls.maxDistance = 1000;

        // Start top-down
        this.camera.position.set(0, 100, 0);
        this.camera.lookAt(0, 0, 0);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }

    fitToBoard(boardWidth, boardHeight) {
        const maxDim = Math.max(boardWidth, boardHeight);
        const fov = this.camera.fov * (Math.PI / 180);
        let distance = (maxDim / 2) / Math.tan(fov / 2);
        distance *= 1.3;
        this.camera.position.set(0, distance, 0);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }

    viewTop()     { this._setView(0, 1, 0); }
    viewBottom()  { this._setView(0, -1, 0); }
    viewFront()   { this._setView(0, 0, 1); }
    viewBack()    { this._setView(0, 0, -1); }
    viewLeft()    { this._setView(-1, 0, 0); }
    viewRight()   { this._setView(1, 0, 0); }

    _setView(x, y, z) {
        const dist = this.camera.position.length() || 100;
        this.camera.position.set(x * dist, y * dist, z * dist);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }

    update() {
        this.controls.update();
    }
}
