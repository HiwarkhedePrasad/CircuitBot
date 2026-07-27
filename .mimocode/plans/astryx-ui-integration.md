# Astryx UI Integration Plan for CircuitBot

## Current State
- Frontend: Vanilla HTML/CSS/JS (no React, no build system)
- Styling: Custom CSS with dark theme (Flux-inspired)
- Architecture: Static files served by Flask

## Integration Strategy
Since CircuitBot uses vanilla JS (not React), we'll extract Astryx design tokens and CSS patterns to enhance the existing UI. This gives us the design system's polish without a full rewrite.

## Phase 1: Extract Design Tokens
1. Create `static/astryx-tokens.css` with Astryx CSS custom properties
2. Map existing CircuitBot colors to Astryx token system
3. Add Astryx typography scale
4. Add Astryx spacing system

## Phase 2: Update Component Styles
1. Buttons - use Astryx button patterns
2. Cards - use Astryx card patterns  
3. Forms/Inputs - use Astryx form patterns
4. Navigation - use Astryx nav patterns
5. Tables - use Astryx table patterns

## Phase 3: Add Missing Components
1. Command palette (for slash commands)
2. Toast notifications
3. Modal dialogs
4. Tooltips

## Files to Modify
- `static/style.css` - Main stylesheet
- `static/index.html` - Add Astryx CSS imports
- New: `static/astryx-tokens.css` - Design tokens
- New: `static/astryx-components.css` - Component patterns

## Verification
1. Visual review of all pages
2. Test slash command autocomplete
3. Verify dark/light theme works
