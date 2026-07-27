/**
 * constants_3d.js — Colors, materials, and layer stackup for the 3D PCB viewer.
 */
const PCB3D = {
    // Board layer Y offsets (mm) for a standard 2-layer board (1.6mm thick)
    LAYER_Y: {
        B_SILK:   -0.82,
        B_MASK:   -0.78,
        B_CU:     -0.72,
        SUB_BOT:  -0.80,
        SUB_TOP:   0.80,
        F_CU:      0.72,
        F_MASK:    0.78,
        F_SILK:    0.82,
    },

    // PCB colors
    COLORS: {
        SUBSTRATE:      0x2d5a27,  // FR-4 green
        COPPER:         0xcc8833,  // Copper gold
        COPPER_B:       0xbb7722,  // Bottom copper (slightly different)
        SOLDER_MASK:    0x1a6b1a,  // Dark green mask
        SOLDER_MASK_B:  0x1a5a1a,
        SILKSCREEN:     0xeeeeee,  // White silkscreen
        PAD:            0xccaa44,  // Gold pads
        DRILL:          0x333333,  // Dark drill holes
        VIA:            0xcc8833,
        BOARD_EDGE:     0x888888,
    },

    // Component placeholder colors by category
    COMP_COLORS: {
        RESISTOR:   0x222222,  // Black body
        CAPACITOR:  0x8B6914,  // Tan/brown
        IC:         0x111111,  // Black
        CONNECTOR:  0x4444aa,  // Blue
        LED:        0xff3333,  // Red
        CRYSTAL:    0x888888,  // Silver
        INDUCTOR:   0x446644,  // Dark green
        DIODE:      0x222222,  // Black
        GENERIC:    0x555555,  // Gray
    },

    // Default board dimensions
    DEFAULT_THICKNESS: 1.6,  // mm

    // Wireframe
    WIREFRAME_COLOR: 0x4488ff,
};
