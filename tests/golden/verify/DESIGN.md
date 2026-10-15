---
version: alpha
name: Acme Cloud Analysis
description: Bright white canvas, one indigo action color, Space Grotesk display type over Inter, soft 8px controls.
colors:
  primary: "#4f46e5"
  on-primary: "#ffffff"
  canvas: "#ffffff"
  surface: "#f8fafc"
  surface-inverse: "#0b1020"
  hairline: "#e2e8f0"
  ink: "#0f172a"
  muted: "#475569"
  faint: "#94a3b8"
  success: "#16a34a"
typography:
  display:
    fontFamily: Space Grotesk
    fontSize: 64px
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: -1.5px
  heading:
    fontFamily: Space Grotesk
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: -0.8px
  title:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.33
  body-lg:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
  ui:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: 500
    lineHeight: 1.33
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.43
  code:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  caption:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  full: 9999px
spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 20px
  xl: 24px
  xxl: 32px
  xxxl: 48px
  4xl: 64px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 44px
  button-primary-hover:
    backgroundColor: "#4338ca"
  button-secondary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 44px
  button-secondary-hover:
    backgroundColor: "#1e293b"
  button-outline:
    textColor: "{colors.ink}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 11px 19px
    height: 44px
  button-outline-hover:
    backgroundColor: "{colors.surface}"
  button-ghost:
    textColor: "{colors.primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 8px 12px
    height: 36px
  button-ghost-hover:
    backgroundColor: "#eef2ff"
  text-link:
    textColor: "{colors.primary}"
  text-link-hover:
    textColor: "#4338ca"
  nav-link:
    textColor: "{colors.muted}"
    typography: "{typography.ui}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
    height: 36px
  nav-link-hover:
    textColor: "{colors.ink}"
  text-input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 10px 14px
    height: 44px
  card:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.xl}"
    padding: "{spacing.xxl}"
  badge:
    backgroundColor: "#eef2ff"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    padding: 4px 12px
    height: 28px
  top-nav:
    backgroundColor: "#ffffffcc"
    height: 64px
    padding: 0px 24px
  footer:
    backgroundColor: "{colors.surface-inverse}"
    textColor: "{colors.faint}"
    typography: "{typography.body-sm}"
    padding: 64px 0px 48px
---
# Acme Cloud — Design Reference

> Source: https://acme.example/ · Extracted: 2026-10-15 · Pages analyzed: home
>
> Reference only: an automated extraction made to study the visual style. It is not an official
> design system and contains no logos or copyrighted assets.

## Overview

A bright white canvas (`{colors.canvas}` — #ffffff) carries dark slate text (`{colors.ink}` —
#0f172a) and a single indigo action color (`{colors.primary}` — #4f46e5). Display headlines use
Space Grotesk; everything else is set in Inter.

## Colors

- `{colors.primary}` — #4f46e5: primary buttons, links and badges.
- `{colors.on-primary}` — #ffffff: text on primary and inverse surfaces.
- `{colors.surface}` — #f8fafc and `{colors.hairline}` — #e2e8f0 build the tonal steps.
- `{colors.muted}` — #475569 and `{colors.faint}` — #94a3b8 for secondary text.
- `{colors.success}` — #16a34a for positive notes only.

Dark theme: in the dark capture the muted gray #64748b on #05070f measures only 4.23:1.

## Typography

`{typography.display}` (64px Space Grotesk 700) down to `{typography.caption}` (13px Inter).
Body copy uses `{typography.body}`; buttons and navigation use `{typography.ui}`.

## Layout

Content sits in a 1200px container on a 4px base unit (`{spacing.md}` — 16px is the default gap).
Breakpoints measured from `@media`: 640px, 768px, 1024px and 1280px.

### Responsive Strategy

The top navigation collapses to a hamburger below 1024px; the feature grid goes from 3 to 2 to
1 column.

## Elevation & Depth

Depth comes from tonal surface steps plus two soft shadows on cards and primary buttons.

## Shapes

Controls use `{rounded.md}` (8px), inputs `{rounded.sm}` (6px), cards `{rounded.xl}` (16px) and
badges `{rounded.full}`.

## Components

`{components.button-primary}` turns #4338ca on hover (`{components.button-primary-hover}`);
`{components.card}` pairs `{colors.canvas}` with a 1px `{colors.hairline}` border.

## Do's and Don'ts

- Do keep `{colors.primary}` for the single most important action per screen.
- Don't set caption text in `{colors.faint}` on `{colors.canvas}`: it measures 2.56:1, below
  WCAG AA.
- Don't use the footer copyright gray #64748b on `{colors.surface-inverse}` for body copy:
  3.98:1 passes only for large text.
- Don't write small text in `{colors.success}` on `{colors.canvas}`: 3.3:1 passes only for large
  text.

## Agent Prompt Guide

Build on `{colors.canvas}` with `{colors.ink}` text, use `{components.button-primary}` for the
main action and keep radii on the `rounded` scale.
