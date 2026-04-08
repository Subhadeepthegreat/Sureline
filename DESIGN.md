# Design System — Sureline

## Product Context
- **What this is:** A real-time voice agent that answers enterprise data queries over phone/web
- **Who it's for:** Enterprise clients (primary), internal devs (secondary). Demo surface for prospective clients via WhatsApp/Discord screen-share.
- **Space/industry:** Enterprise AI / voice agents / Indian B2B SaaS
- **Project type:** Single-screen voice interface (orb) + admin/data-setup shell

## Aesthetic Direction
- **Direction:** Organic/Natural meets Art Deco — Indian pigment art, not tech UI
- **Decoration level:** Intentional — alpona geometry, Devanagari accents, ink texture. Present but never decorative for its own sake.
- **Mood:** Calm authority. The product knows more than you — it does not need to shout. Like a scholar's study, not a startup demo. Warm, grounded, slightly ceremonial.
- **Reference:** `ChatGPT Image Apr 4, 2026, 05_10_49 PM.png` in project root — north star for the orb screen.
- **Anti-patterns:** Cool tones in the lotus (no blue-gray), generic SaaS dark mode, neon glows, purple gradients, uniform flat ellipse petals.

## Typography
- **Display/Hero:** Cormorant Garamond — italic, weight 300-400. Evokes Indian manuscript tradition, literary authority.
- **Body/UI:** DM Sans — weight 300-500. Clean, legible at small sizes, does not fight the serif.
- **Code/Mono:** JetBrains Mono — technical metadata, session info, latency numbers.
- **Loading:** Google Fonts CDN — Cormorant+Garamond + DM+Sans + JetBrains+Mono
- **Scale:**
  - Display: 64-96px / Cormorant italic 300
  - H1: 36-48px / Cormorant italic 400
  - H2: 22-28px / Cormorant 500
  - Body: 15px / DM Sans 400, line-height 1.7
  - Small/Label: 13px / DM Sans 400
  - Mono/Meta: 11px / JetBrains Mono 400, letter-spacing 0.1-0.15em
  - Status text (below orb): 20-22px / Cormorant italic 400

## Color
- **Approach:** Warm earth pigments throughout. Zero cool tones in the lotus. Cool frame blue is UI chrome only (never in the orb).
- **Backgrounds:**
  - `--bg-base: #0d0f14` — ink black (slightly warm, not pure #000)
  - `--bg-surface: #151821` — elevated surface
  - `--bg-elevated: #1e2230` — cards, inputs
  - `--bg-orb-glow: #150d08` — deep warm near-black for orb radial glow (updated from #1a0f22: warmer, less purple)
- **Text:**
  - `--text-primary: #f0ead8` — warm parchment white (not cool white)
  - `--text-muted: #6b7094` — muted label color
- **Lotus / Orb palette — warm earth pigments, NO cool tones:**
  - `--lotus-outer: #8a5a6a` — deep dried rose, outer petals
  - `--lotus-outer-light: #c47a8a` — lotus rose, outer petals highlight
  - `--lotus-mid: #c47a8a` — lotus pink, mid ring
  - `--lotus-inner: #b86a4e` — terracotta, inner petals
  - `--lotus-inner-warm: #d4835a` — warm terracotta highlight
  - `--lotus-center: #c49a2a` — turmeric gold, center
  - `--lotus-center-glow: #e8c060` — bright gold center point
- **UI chrome — cool, used sparingly, never in the orb:**
  - `--accent-data: #4a6b65` — teal-green for status/success
  - `--accent-frame: #3d4a6e` — frame borders, subtle dividers
  - `--accent-frame-light: #5a6e9e` — active nav, section labels
- **Alpona geometry:** rgba(220,213,196,0.06) — chalk-white at very low opacity
- **Semantic:** success #4a6b65, warning #c49a2a, error #b86a4e, info #5a6e9e
- **Dark mode:** This IS dark mode. No light mode — the product is always candlelit.

## Lotus Construction Rules
The lotus SVG is the product's identity.

1. **Petal shape:** Teardrop bezier paths, NOT ellipses. Each petal: wide base, tapered tip, rotated around center with `rotate(N * 360/count) translate(0, -offsetRadius)`.
2. **Three concentric rings:** outer (8 petals, largest radius), mid (8 petals, offset 22.5deg), inner (6-8 petals, smallest radius).
3. **Petal opacity:** outer 0.75, mid 0.85, inner 0.9. Overlap creates implied depth.
4. **Per-petal gradient:** Radial gradient from lighter tip to deeper base — simulates petal curvature.
5. **Color by ring:** outer = lotus-outer/lotus-outer-light, mid = lotus-mid, inner = lotus-inner/lotus-inner-warm. ALL warm. No blue, no gray.
6. **Center:** Turmeric gold circle with radial glow filter (feGaussianBlur + feComposite).
7. **Background geometry:** Faint chalk-white alpona rings at 1.1x, 1.3x, 1.6x the outer petal radius. Opacity 0.05-0.08. These ground the orb in space.

## Orb Screen Layout
The single screen that matters most:

```
+--------------------------------------------------+
|  LIVE  Sureline                   session 00:00  |  <- chrome top (JetBrains Mono 11px)
+--------------------------------------------------+
|                                                  |
|         [alpona SVG geometry layer]              |
|                                                  |
|                 [LOTUS ORB]                      |  <- center, 340-380px
|                                                  |
|             Listening...                         |  <- Cormorant italic 22px
|   "What was last quarter's revenue?"             |  <- DM Sans 14px muted, transcript
|                                                  |
| [Idle] [Listening] [Processing] [Speaking]       |  <- dev-only state controls
|                                                  |
+--------------------------------------------------+
|  Mahakash Retail  v0.1                End call   |  <- chrome bottom
+--------------------------------------------------+
```

- Radial background: `radial-gradient(ellipse 80% 70% at 50% 50%, #150d08 0%, #0d0f14 65%)`
- Alpona layer: SVG positioned absolute behind orb, full viewport, very low opacity
- Orb centered at ~45% height (slightly above center feels more commanding)

## Animation States
- **Idle:** scale(0.97-1.02) breathe, 4s ease-in-out, staggered per ring (0.3s offsets). Meditative.
- **Listening:** Faster breathe 1.2s + 3 expanding wave rings in lotus-rose. Rings expand from orb edge to 1.4x, opacity 0.7 to 0.
- **Processing:** Outer ring slow rotate (8s linear). Center gold brightens. No wave rings.
- **Speaking:** Bloom to scale(1.12) + fast wave rings (1.2s). The lotus fully opens.
- **Transitions:** 400ms ease-in-out between states. Status text cross-fades (300ms).

## Spacing
- **Base unit:** 8px
- **Scale:** 4 / 8 / 16 / 24 / 32 / 48 / 64
- **Orb screen:** Spacious — minimum 40px padding. The orb needs room.
- **Admin/data screens:** Comfortable — 24px base, 12px inner card padding.

## Layout
- **Orb screen:** Full viewport, single centered column. One job only.
- **Admin screens:** 220px sidebar + main content. Max width 1200px.
- **Border radius:** UI elements 3-4px (sharp, not bubbly). Badges 2px. Orb: none (circle).

## Motion
- **Approach:** Intentional — every animation has meaning. The lotus IS the animation.
- **Easing:** cubic-bezier(0.4, 0, 0.2, 1) for state transitions; ease-in-out for breathe.
- **Duration:** idle breathe 4000ms, state transition 400ms, wave expansion 2000ms idle / 1200ms speaking, text fade 300ms.
- **Rule:** No decorative motion outside the orb. The orb moves; the UI is still.

## Integration Principles
What makes the design feel whole vs isolated:

1. **Alpona layer is not optional.** Bare lotus on plain background is unfinished. The SVG geometry must always be present at low opacity.
2. **Background glow must be warm-purple (#1a0f22 center).** This is what makes the lotus belong to its background.
3. **Petal colors must be warm throughout.** The blue-gray (#3d4a6e) is UI chrome ONLY. Never in the lotus, never in the orb background.
4. **Status text uses Cormorant italic.** This is the agent's voice — should feel like speech, not a system label.
5. **Wave rings use lotus-rose, not white.** rgba(196,122,138,0.3) — expansion feels like petals opening, not sonar.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-07 | Cormorant Garamond + DM Sans | Serif authority for voice/status, clean sans for data/labels |
| 2026-04-07 | All-warm lotus palette, cool only for chrome | Reference image has zero cool tones in orb — blue-gray was a design system leftover |
| 2026-04-07 | Teardrop petals over ellipses | Ellipses make a daisy; teardrops make a lotus. Shape IS the identity. |
| 2026-04-07 | Alpona geometry layer required | Orb feels isolated without it — geometry grounds it in Indian visual tradition |
| 2026-04-07 | No light mode | The product lives in enterprise context — always candlelit |
| 2026-04-07 | Created by /design-consultation | Based on Voice-animation.md + reference image (ChatGPT Image Apr 4, 2026) |
| 2026-04-08 | Lotus density: 8→12 outer/mid petals, hw +30% | Sparse 8-petal structure read as daisy not lotus vs reference |
| 2026-04-08 | feDropShadow on outer/mid/inner rings | Creates 3D petal layering without texture overlay — warm flood-color #1a0800 |
| 2026-04-08 | 5th ray corona ring (16 petals, orbitR=98) | Spiky outer corona visible in reference, adds intricate geometry |
| 2026-04-08 | bgGlow center opacity 0.40→0.62 | Reference halo much stronger — was visually weak |
| 2026-04-08 | Wave ring 3: cool-indigo→warm-dark rgba(100,40,20,0.22) | Ring 3 is shadow ring — should be warm shadow not cool chrome |
| 2026-04-08 | Mobile bottom nav (Monitor/Agent/Data/Setup) | Admin sidebar hides on mobile; bottom nav replaces navigation |
| 2026-04-08 | Client login screen designed | Split layout: lotus brand panel left + form right. SSO primary, email/password secondary |
| 2026-04-08 | bg-orb-glow corrected #1a0f22→#150d08 | #150d08 warmer/darker, matches reference more closely |
