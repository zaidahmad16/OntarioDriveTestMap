---
name: OntarioDriveTestMap
description: Crowdsourced, GPS-verified Ontario G/G2 road test routes
colors:
  ink: "#181c20"
  ink-muted: "#57616b"
  ink-faint: "#8a939c"
  bg: "#f3f5f7"
  surface: "#ffffff"
  surface-sunken: "#eceff2"
  border: "#d7dce1"
  border-strong: "#b8c0c8"
  accent: "#1f4e79"
  accent-hover: "#163a5c"
  accent-tint: "#e8eef4"
  confirmed: "#c0392b"
  confirmed-tint: "#fbeceb"
  predicted: "#8e44ad"
  predicted-tint: "#f5eefa"
  success: "#1e8e5a"
  success-tint: "#e7f6ef"
  warning: "#c07c15"
  warning-tint: "#fbf1e1"
  gap: "#6b7680"
  gap-tint: "#eef0f2"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "clamp(1.75rem, 3vw, 2.25rem)"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.005em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.01em"
  data:
    fontFamily: "ui-monospace, 'SF Mono', Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "20px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "10px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-sunken}"
  badge-confirmed:
    backgroundColor: "{colors.confirmed-tint}"
    textColor: "{colors.confirmed}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
  badge-predicted:
    backgroundColor: "{colors.predicted-tint}"
    textColor: "{colors.predicted}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "16px"
---

# Design System: OntarioDriveTestMap

## 1. Overview

**Creative North Star: "The Field Notebook."**

Picture a careful examiner's own notebook: real streets, real turns, real timestamps, written down exactly as driven, with no invented flourish and no doubt left unmarked. That is the whole personality of this interface. It is a civic utility used at a moment of real stress, a driver's-test candidate checking a route the night before, or a passenger reading turns aloud from the passenger seat, not a product being sold. Every design choice optimizes for "can I trust this, right now, at a glance" over "does this look impressive."

This system explicitly rejects the visual language of its own competitors, confirmed by direct audit: template-clone booking sites that dress up a fake sample route in confident marketing chrome, and schematic maps that look precise while being drawn from memory. Nothing here should ever look more certain than the data actually is. Where a competitor would smooth a rough edge into a glossy stat card, this system shows the real, sometimes-uneven texture of the source data instead, because that texture is the actual evidence of trustworthiness.

It also rejects generic SaaS product polish: no gradient hero banners, no big glossy KPI tiles, no marketing urgency. The existing app already encodes a real, load-bearing color language for data trust (confirmed vs. predicted vs. gap), established under real incident pressure; this system does not reinvent those meanings, it gives them a coherent home alongside a calm, civic-toned UI chrome built around them.

**Key Characteristics:**
- Calm, high-legibility neutral canvas; color is reserved for meaning, not decoration
- One civic-blue accent for generic actions and navigation, kept visually distinct from the data-trust palette so the two systems never get confused
- A dedicated, unambiguous five-color trust-tier vocabulary (confirmed / predicted / success / warning / gap) used identically everywhere a route or fact is shown
- Typography built for scanning under pressure: strong hierarchy, generous line height, a monospace treatment for real measured data (distances, durations, coordinates) that visually signals "this number was measured, not estimated"
- Flat by default; elevation is used sparingly and only to lift transient UI (popovers, the notification bell, modals) above the map

## 2. Colors

The palette splits into two systems that must never blend: **UI chrome** (neutral canvas + one civic accent, used for navigation, actions, and structure) and **data trust** (the five-color vocabulary that reports how certain a piece of route data actually is). A button or link is never colored using a trust-tier color, and a trust-tier fact is never colored using the accent; keeping the two vocabularies visually distinct is what makes the trust colors legible as *meaning* rather than *style*.

### Primary
- **Civic Blue** (`#1f4e79`): the one UI accent. Primary buttons, active nav/tab states, links, focus rings. Deliberately not a generic SaaS blue or the same hue as any trust color, evoking an official, dependable civic tool rather than a startup product. Used sparingly: most of a screen should be neutral.

### Neutral
- **Ink** (`#181c20`): primary text and icons. A soft near-black with a faint cool cast, easier to sustain than pure black across long instruction tables.
- **Ink Muted** (`#57616b`): secondary text, captions, metadata (timestamps, author counts). Passes 4.5:1 on both `Cloud` and `Surface White`.
- **Ink Faint** (`#8a939c`): tertiary/disabled text and placeholder copy only; never body text.
- **Cloud** (`#f3f5f7`): page background. A true cool near-white, not a warm cream, matching the tool's civic register rather than an editorial-warm one.
- **Surface White** (`#ffffff`): panels, cards, the map container, popovers, sitting one layer above Cloud.
- **Surface Sunken** (`#eceff2`): inset wells inside a panel (code-like blocks, quoted excerpts, disabled inputs).
- **Border** (`#d7dce1`) / **Border Strong** (`#b8c0c8`): hairline dividers and default input borders / emphasized borders (selected states, active tab underline).

### Data Trust Palette (Named Rule)
**The Never-Blur Rule.** Confirmed and predicted data are never rendered in a way that makes them visually indistinguishable, even under pressure to simplify. Every route line, badge, table row, and popup that carries a trust tier uses one of these five colors, consistently, everywhere:

- **Confirmed** (`#c0392b` / tint `#fbeceb`): real, GPS-verified route geometry and "hand-verified" badges. The single highest-trust signal in the product.
- **Predicted** (`#8e44ad` / tint `#f5eefa`): road-snapped guesses bridging two real points nobody drove between. Always paired with the word "predicted", never color alone.
- **Success** (`#1e8e5a` / tint `#e7f6ef`): affirmative states unrelated to route geometry itself, form submitted, report sent, an "Easy" difficulty rating, a promoted community submission.
- **Warning** (`#c07c15` / tint `#fbf1e1`): moderate caution, a "Moderate" difficulty rating, an off-route deviation during drive-along practice.
- **Gap** (`#6b7680` / tint `#eef0f2`): honestly-flagged missing data, an unrouted straight-line jump, a below-threshold segment. Deliberately the least saturated of the five: an absence of evidence, not a claim.

## 3. Typography

**UI Font:** Inter (with `system-ui, sans-serif` fallback)
**Data Font:** ui-monospace (with `'SF Mono', Consolas, monospace` fallback)

**Character:** One clean, function-first grotesque doing every job from page titles to form labels, so the interface never competes with the content for attention, paired with a monospace treatment reserved for real measured facts. The mono face is not decorative: seeing a distance or duration in mono is a quiet, learnable signal that the number came from the road, not from a guess.

### Hierarchy
- **Display** (700, `clamp(1.75rem, 3vw, 2.25rem)`, 1.15): page-level headers only (the app title). Used once per page.
- **Headline** (600, 1.25rem, 1.3): section headers (a centre's name once selected, "Discussion", "Compare centres").
- **Title** (600, 1rem, 1.4): component-level headers (a route panel's summary line, a card title, a form legend).
- **Body** (400, 0.9375rem, 1.55): all prose, instructions, form copy. Capped at 70ch wherever it wraps freely.
- **Label** (600, 0.8125rem, 1.3, +0.01em tracking): button text, table headers, form field labels, badges. Small and confident, not stretched into an all-caps eyebrow.
- **Data** (500, 0.875rem, 1.4, monospace): distances, durations, speeds, coordinates, dates in tables. Right-aligned in tabular contexts.

### Named Rules
**The Measured-Fact Rule.** Any number that came from a real measurement (OSRM distance, GPS duration, a posted speed limit) renders in the Data (monospace) style. Any number that's a count or a UI label (a route index, a badge count) stays in the UI font. This is how a reader tells "real" from "counted" at a glance, without reading the label.

## 4. Elevation

Flat by default. This system uses two, not sixteen, levels: the page canvas (Cloud) and one lifted surface (Surface White) for everything that reads as a distinct object, panels, cards, tables, the map. A third, genuinely floating level exists only for transient overlays that must sit above the map and everything else: popovers (the notification bell, the reminder button, dropdown menus) and modals (the route submission wizard). Nothing else gets a shadow; depth on static content is conveyed by the Surface/Cloud contrast and hairline borders, not by drop shadows.

### Shadow Vocabulary
- **Overlay** (`box-shadow: 0 8px 24px rgba(20, 24, 28, 0.14)`): popovers and dropdown panels. Paired with a 1px `Border` outline so it reads crisply even on a low-contrast display.
- **Modal** (`box-shadow: 0 16px 48px rgba(20, 24, 28, 0.22)`): full modals over a dimmed backdrop (`rgba(20, 24, 28, 0.45)`).

### Named Rules
**The Flat-Ground Rule.** If content is part of the page's normal reading flow, it has no shadow, only a border or background shift. A shadow appears exclusively on content that has left the document flow to float above it.

## 5. Components

### Buttons
- **Shape:** 6px corner radius (`{rounded.sm}`), never fully square, never pill-shaped except badges.
- **Primary:** Civic Blue background, white text, `10px 18px` padding, Label typography. Used once per view for the single most important action (submit a route, start drive-along practice).
- **Secondary:** Surface White background, Ink text, 1px Border. Default for everything else, most buttons in this app are secondary.
- **Hover / Focus:** Primary darkens to Accent Hover; secondary fills with Surface Sunken. Focus-visible always shows a 2px Civic Blue outline, offset 2px, regardless of button variant, never suppressed.
- **Destructive (delete, remove):** Secondary shape, Confirmed-red text and border on hover only, to avoid a fully red button reading as a route-trust signal.

### Badges (Trust Pills)
- **Style:** pill radius (`{rounded.pill}`), tinted background matched to its trust color, same-hue text, `3px 10px` padding, Label typography, always paired with a short word ("verified", "predicted"), never color alone.

### Cards / Panels
- **Corner Style:** 10px radius (`{rounded.md}`).
- **Background:** Surface White on Cloud.
- **Shadow Strategy:** none at rest (see Elevation's Flat-Ground Rule); a 1px Border instead.
- **Internal Padding:** 16px (`{spacing.lg}` at 20px for larger panels like the route summary card).

### Inputs / Fields
- **Style:** Surface White background, 1px Border, 6px radius, 8-10px vertical padding.
- **Focus:** Border shifts to Civic Blue, 2px, no glow/box-shadow bloom (keeps the flat, precise feel).
- **Error:** Border and helper text shift to Confirmed-red tint colors (borrowed for form validation only, never implying route data).
- **Disabled:** Surface Sunken background, Ink Faint text.

### Navigation
- **Style:** the app header is a single flat row on Surface White, 1px Border beneath it, Ink text at Body weight, active/current state underlined in Civic Blue rather than filled, keeping the header visually quiet under Cloud-toned pages.
- **Mobile:** the header row wraps rather than truncates; the language toggle and notification bell always stay visible, secondary actions (submit route, discussion link, reminder) collapse into a compact overflow row below the title on narrow viewports.

### Route Trust Banner (signature component)
A full-width, tinted notice strip (trust-tint background, trust-color 1px border, trust-color text for the key phrase only) that appears directly under a route's summary whenever that route contains gaps or predicted segments. This is the single most important custom component in the app: it is the concrete implementation of the Never-Blur Rule, and it must never be dismissible, collapsible, or reduced to an icon.

## 6. Do's and Don'ts

### Do:
- **Do** keep the data-trust palette (confirmed / predicted / success / warning / gap) completely separate from the Civic Blue UI accent; a button is never trust-colored and a trust badge is never accent-colored.
- **Do** pair every trust color with a plain-language word ("confirmed", "predicted", "unrouted"). Color alone is not an accessible signal and is not sufficient for this app's own stated safety principle.
- **Do** use the monospace Data style for every real measured number (distance, duration, speed, date), consistently, so it becomes a learnable trust signal in itself.
- **Do** default to flat surfaces with a hairline border; reserve shadows for content that has actually left the page's document flow (popovers, modals).
- **Do** design every screen for a narrow phone viewport first; this product is used one-handed, often by an anxious test-taker or a passenger reading aloud.

### Don't:
- **Don't** soften or hide the predicted/gap/confirmed distinction for visual cleanliness, under any circumstance, including a request to simplify. This is a previously-litigated, non-negotiable product rule, not a style preference.
- **Don't** use gradient hero banners, glossy KPI stat cards, or any SaaS-marketing-page chrome. This is a utility tool, not a product landing page.
- **Don't** use border-left/border-right color stripes as an accent on cards or list rows; use full borders, tinted backgrounds, or leading badges instead.
- **Don't** apply gradient text, glassmorphism, or decorative blur anywhere in this system.
- **Don't** stretch Label typography into a tiny all-caps tracked "eyebrow" above every section; this system uses at most one deliberate section label per screen, never as default scaffolding.
- **Don't** introduce a second UI accent hue. One Civic Blue for all generic actions; new semantic meanings get a new named trust color instead of borrowing the accent.
