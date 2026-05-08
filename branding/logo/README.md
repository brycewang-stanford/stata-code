# stata-code logo — v4 (coef-plot · Anthropic clay)

Final brand kit. Mark = a small forest / coefficient plot: dashed zero axis,
three estimates with whiskers, three orange point estimates. Speaks directly
to the econometrics audience and reads as a "developer tool", not a toy.

## Design tokens

| Token | Value | Notes |
| --- | --- | --- |
| Ink | `#0F172A` | Wordmark, axis & whiskers on light, dark cards |
| Anthropic clay | `#CC785C` | Point estimates, accent only |
| Cream | `#FEFAF7` | Light card background, axis & whiskers on dark |
| Wordmark face | `ui-monospace, 'SF Mono', Menlo, Consolas, monospace` | bold 700, letter-spacing −0.5 |

The wordmark is monospace on purpose — `stata-code` with the `-code` half is a
developer tool, and the typography reflects that.

## Files

### Marks (icon only)

| File | Use |
| --- | --- |
| [`mark.svg`](mark.svg) | Transparent background — drop into anything light |
| [`mark-on-cream.svg`](mark-on-cream.svg) | Self-contained cream card — app icon, tile, kernel logo |
| [`mark-on-dark.svg`](mark-on-dark.svg) | Self-contained dark card — dark UI, OG strip |
| [`mark-mono-ink.svg`](mark-mono-ink.svg) | Single-color stamp — etch, embroidery, B&W print |

### Lockups (icon + wordmark)

| File | Use | viewBox |
| --- | --- | --- |
| [`horizontal.svg`](horizontal.svg) | README hero on light surfaces | 320 × 80 |
| [`horizontal-on-dark.svg`](horizontal-on-dark.svg) | README hero on dark / banner | 320 × 80 |
| [`stacked.svg`](stacked.svg) | Square / portrait contexts | 200 × 160 |

### Web assets

| File | Use |
| --- | --- |
| [`favicon.svg`](favicon.svg) | Vector favicon (drop the dashed axis for legibility under 32px) |
| [`favicon-16.png`](favicon-16.png) / [`favicon-32.png`](favicon-32.png) | Raster favicons |
| [`apple-touch-icon-180.png`](apple-touch-icon-180.png) | iOS home-screen icon |
| [`social-card.svg`](social-card.svg) / [`social-card@1200.png`](social-card@1200.png) | 1200×630 OG / Twitter card |

PNG renders at @512 / @1024 / @1280 sit alongside each SVG.

## Embedding

```markdown
<!-- README hero -->
<p align="center">
  <img src="branding/logo-v4/horizontal.svg" alt="stata-code" width="420" />
</p>
```

```html
<!-- HTML head -->
<link rel="icon" type="image/svg+xml" href="/branding/logo-v4/favicon.svg" />
<link rel="icon" type="image/png" sizes="32x32" href="/branding/logo-v4/favicon-32.png" />
<link rel="apple-touch-icon" href="/branding/logo-v4/apple-touch-icon-180.png" />
<meta property="og:image" content="https://.../branding/logo-v4/social-card@1200.png" />
```

## Don'ts

- Don't recolor the dots away from `#CC785C` — that's the only accent.
- Don't add gradients, drop shadows, or glows.
- Don't stretch the lockups; scale uniformly.
- Don't put the dark variant on near-black surfaces — use ≥ `#1E293B` or it disappears.
