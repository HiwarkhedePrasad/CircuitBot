---
name: css-frontend-debug
description: Diagnose and fix CSS not loading, not rendering, or not applying in web projects. Use when the user reports styles missing, blank pages, unstyled content, or CSS files not being picked up. Covers import path issues, bundler config, missing links, and framework-specific gotchas.
---

# CSS / Frontend Debugging

Systematic workflow for diagnosing why CSS is not loading or rendering in a web project.

## When to Use

- User reports "CSS is not loading" or "styles are not showing"
- Page renders with no styling (plain HTML look)
- CSS file exists but styles don't apply
- Framework transition issues (e.g., Next.js Pages Router vs App Router)
- Build succeeds but output has no styles

## Diagnostic Workflow

### Step 1: Identify the Project Type

Check for framework indicators:
- `package.json` with `next` → Next.js
- `package.json` with `vite` → Vite
- `package.json` with `react-scripts` → CRA
- `index.html` with `<link rel="stylesheet">` → Static HTML
- `tailwind.config.*` → Tailwind CSS

### Step 2: Check CSS Import Chain

**Static HTML:**
```html
<!-- Verify the href path is correct relative to the HTML file -->
<link rel="stylesheet" href="./styles.css">
<!-- NOT href="/styles.css" (breaks on file:// protocol) -->
```

**React/Next.js (App Router):**
```jsx
// app/layout.js or app/page.js
import './globals.css';  // Must be a relative import, NOT a <link> tag
```

**React/Next.js (Pages Router):**
```jsx
// pages/_app.js
import '../styles/globals.css';
```

**Common mistake:** Using `<link>` tags in JSX instead of CSS imports in the entry file.

### Step 3: Check Build/Dev Server Output

Run the dev server and look for errors:
```bash
# Next.js
npm run dev 2>&1 | head -50

# Vite
npm run dev 2>&1 | head -50

# Check for CSS-related warnings in build output
npm run build 2>&1 | grep -i "css\|style\|module"
```

### Step 4: Check Framework-Specific Gotchas

**Next.js App Router:**
- CSS must be imported in a Client Component (`"use client"`) or in `layout.js`
- `globals.css` must be imported in `app/layout.js`, not in individual pages
- CSS Modules (`*.module.css`) only work in Client Components

**Tailwind CSS:**
- Verify `@import "tailwindcss"` or `@tailwind` directives are present
- Check `tailwind.config.content` includes all template paths
- Tailwind v4 uses `@import "tailwindcss"` not `@tailwind base; @tailwind components; @tailwind utilities;`

**Vite:**
- CSS imports in JSX work out of the box
- Check `vite.config.js` for CSS preprocessor options

### Step 5: Check File Path Resolution

```bash
# Verify the CSS file exists where expected
ls -la styles/
ls -la src/styles/
ls -la app/

# Check if the file is being watched by the dev server
# Look for "[wcss]" or "[vite]" or "[next]" messages about CSS
```

### Step 6: Check for Build Cache Issues

```bash
# Clear build caches
rm -rf .next        # Next.js
rm -rf dist         # Vite/Webpack
rm -rf node_modules/.cache
npm run dev         # Restart dev server
```

### Step 7: Verify in Browser DevTools

If accessible, check:
- Network tab: Is the CSS file being requested? What's the status code?
- Elements tab: Is the `<link>` or `<style>` tag present in the DOM?
- Computed tab: Are any styles applied to elements?

## Common Fixes

| Problem | Fix |
|---------|-----|
| `<link href="/styles.css">` on `file://` | Change to `./styles.css` (relative) |
| CSS import in page component, not layout | Move `import './globals.css'` to `layout.js` |
| Tailwind classes not applied | Check `@import "tailwindcss"` is at top of CSS file |
| CSS Module not working | Ensure component has `"use client"` directive |
| Build cache stale | Delete `.next/` or `dist/` and restart |
| Wrong CSS file path | Verify path relative to the importing file |
| PostCSS not processing | Check `postcss.config.js` exists and is correct |

## Verification

After fixing:
1. Restart dev server (`npm run dev`)
2. Hard-refresh browser (Ctrl+Shift+R)
3. Check that styles render correctly
4. Run `npm run build` to verify production build includes styles
