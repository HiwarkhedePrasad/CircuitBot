const PCB_COLORS = {
    background: 0x000000,        // Pure black
    gridMinor: 0x0a0a0a,        // Very subtle grid
    gridMajor: 0x1a1a1a,        // Visible grid
    boardFill: 0x080808,        // Dark board fill
    outline: 0x00ffcc,          // Bright cyan outline
    outlineShadow: 0x004433,
    airwire: 0xffffff,
    airwireDim: 0x8899aa,       // KiCad-style muted blue-gray
    topCopper: 0xff4444,        // Bright red for F.Cu
    bottomCopper: 0x4488ff,     // Bright blue for B.Cu
    copperEdge: 0xffaa77,       // Bright copper edge
    viaCopper: 0xffcc88,        // Bright via copper
    viaDrill: 0x000000,         // Black drill hole
    smdTop: 0xff6655,           // Bright red SMD pads
    smdBottom: 0x5599ff,        // Bright blue SMD pads
    throughPad: 0xffcc88,       // Golden through-hole pads
    exposedPad: 0xffbb99,       // Bright exposed pads
    maskPad: 0x0a2a22,          // Dark mask
    silkscreen: 0xffffff,       // Pure white silkscreen
    silkscreenFill: 0xffffff,   // White fill
    fab: 0x88ccaa,
    fabFill: 0x2a5548,
    courtyard: 0x2a6655,
    text: 0xffffff,             // Pure white text
    textDim: 0x888888,
    selection: 0x00ffcc,        // Bright cyan selection
    routeGhost: 0xffff00,       // Bright yellow route ghost
    hoverFill: 0x0a0a0a,
    hole: 0x000000,             // Black holes
    pin1Marker: 0x00ffcc,       // Bright cyan pin marker
    padNumber: 0xffffff,
    padNumberShadow: 0x000000,
};

const PCB_TEXT_STYLE = {
    fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
    fontSize: 14,
};

const PCB_MODE = {
    IDLE: 'idle',
    PANNING: 'panning',
    DRAG_COMPONENT: 'drag_component',
    ROUTE: 'route',
    GHOST_PLACEMENT: 'ghost_placement',
    DRAW_OUTLINE: 'draw_outline',
};

const PCB_TOOL = {
    PAN: 'pan',
    SELECT: 'select',
    ROUTE: 'route',
    VIA: 'via',
    OUTLINE: 'outline',
};

const PCB_POINTER_DRAG_THRESHOLD_PX = 4;

const PCB_LAYER_CATALOG = [
    { name: 'F.Cu', label: 'TOP', color: '#ff563d', group: 'copper', visible: true },
    { name: 'In1.Cu', label: 'GND1', color: '#d84d4d', group: 'copper', visible: false },
    { name: 'In2.Cu', label: 'SIGNAL1-X', color: '#d98b2b', group: 'copper', visible: false },
    { name: 'In3.Cu', label: 'SIGNAL2-Y', color: '#2fb8aa', group: 'copper', visible: false },
    { name: 'In4.Cu', label: 'GND2', color: '#f05d8f', group: 'copper', visible: false },
    { name: 'In5.Cu', label: 'SIGNAL3-X', color: '#a7a9d6', group: 'copper', visible: false },
    { name: 'In6.Cu', label: 'SIGNAL4-Y', color: '#c88c7a', group: 'copper', visible: false },
    { name: 'In7.Cu', label: 'GND3', color: '#cfd2d8', group: 'copper', visible: false },
    { name: 'In8.Cu', label: 'PWR', color: '#eadb87', group: 'copper', visible: false },
    { name: 'B.Cu', label: 'BOTTOM', color: '#356cff', group: 'copper', visible: true },
    { name: 'F.Adhes', label: 'F.Adhes', color: '#8a00c8', group: 'aux', visible: false },
    { name: 'B.Adhes', label: 'B.Adhes', color: '#3100ba', group: 'aux', visible: false },
    { name: 'F.Paste', label: 'F.Paste', color: '#f2a0a0', group: 'aux', visible: false },
    { name: 'B.Paste', label: 'B.Paste', color: '#2ed3c7', group: 'aux', visible: false },
    { name: 'F.SilkS', label: 'F.SilkS', color: '#f4ef9f', group: 'graphics', visible: true },
    { name: 'B.SilkS', label: 'B.SilkS', color: '#f7f3cf', group: 'graphics', visible: false },
    { name: 'F.Mask', label: 'F.Mask', color: '#8d47aa', group: 'aux', visible: false },
    { name: 'B.Mask', label: 'B.Mask', color: '#3d7f6c', group: 'aux', visible: false },
    { name: 'Dwgs.User', label: 'Dwgs.User', color: '#cfd2d8', group: 'docs', visible: false },
    { name: 'Cmts.User', label: 'Cmts.User', color: '#71a4ef', group: 'docs', visible: false },
    { name: 'Eco1.User', label: 'Eco1.User', color: '#c8ddd7', group: 'docs', visible: false },
    { name: 'Eco2.User', label: 'Eco2.User', color: '#ddd85d', group: 'docs', visible: false },
    { name: 'Edge.Cuts', label: 'Edge.Cuts', color: '#19d7b0', group: 'outline', visible: true },
    { name: 'Margin', label: 'Margin', color: '#ff2ad6', group: 'docs', visible: false },
    { name: 'F.Fab', label: 'F.Fab', color: '#8eb0aa', group: 'graphics', visible: false },
    { name: 'B.Fab', label: 'B.Fab', color: '#8eb0aa', group: 'graphics', visible: false },
    { name: 'F.CrtYd', label: 'F.CrtYd', color: '#3d7570', group: 'graphics', visible: false },
    { name: 'B.CrtYd', label: 'B.CrtYd', color: '#3d7570', group: 'graphics', visible: false },
];
