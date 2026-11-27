"""Swiss-minimalist engineering studio theme, semantic tokens, and SVG-first HTML cards for app.py."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlparse

import gradio as gr


# ---------------------------------------------------------------------------
# Consistent 1.75px stroke outline SVG icons (decorative -> aria-hidden="true")
# ---------------------------------------------------------------------------

_SVG_BRAND = (
    '<svg class="dkb-brand-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true">'
    '<path d="M12 2L3 7l9 5 9-5-9-5z"/>'
    '<path d="M3 17l9 5 9-5"/>'
    '<path d="M3 12l9 5 9-5"/>'
    "</svg>"
)

# Native 16x16 coordinate SVGs for Chatbot placeholder (immune to Gradio's viewBox stripping)
_SVG_BOOK_16 = (
    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M3 13a1.6 1.6 0 0 1 1.6-1.6H13"/>'
    '<path d="M4.6 1.8H13v12.4H4.6A1.6 1.6 0 0 1 3 12.6V3.4a1.6 1.6 0 0 1 1.6-1.6z"/>'
    "</svg>"
)

_SVG_LAYERS_16 = (
    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polygon points="8 1.5 1.8 4.8 8 8.1 14.2 4.8 8 1.5"/>'
    '<polyline points="1.8 11.2 8 14.5 14.2 11.2"/>'
    '<polyline points="1.8 8 8 11.3 14.2 8"/>'
    "</svg>"
)

_SVG_GLOBE_16 = (
    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="8" cy="8" r="6.2"/>'
    '<line x1="1.8" y1="8" x2="14.2" y2="8"/>'
    '<path d="M8 1.8a9.5 9.5 0 0 1 2.5 6.2A9.5 9.5 0 0 1 8 14.2 9.5 9.5 0 0 1 5.5 8 9.5 9.5 0 0 1 8 1.8z"/>'
    "</svg>"
)

_SVG_BOOK = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
    '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'
    "</svg>"
)

_SVG_LAYERS = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polygon points="12 2 2 7 12 12 22 7 12 2"/>'
    '<polyline points="2 17 12 22 22 17"/>'
    '<polyline points="2 12 12 17 22 12"/>'
    "</svg>"
)

_SVG_GLOBE = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10"/>'
    '<line x1="2" y1="12" x2="22" y2="12"/>'
    '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
    "</svg>"
)

_SVG_SHIELD = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
    '<polyline points="9 12 11 14 15 10"/>'
    "</svg>"
)

_SVG_CLOCK = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10"/>'
    '<polyline points="12 6 12 12 16 14"/>'
    "</svg>"
)

_SVG_ACTIVITY = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>'
    "</svg>"
)

_SVG_CHECK = (
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="20 6 9 17 4 12"/>'
    "</svg>"
)

_SVG_ALERT = (
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10"/>'
    '<line x1="12" y1="8" x2="12" y2="12"/>'
    '<line x1="12" y1="16" x2="12.01" y2="16"/>'
    "</svg>"
)

_SVG_EXTERNAL = (
    '<svg class="dkb-ext-svg" width="12" height="12" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true">'
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    '<polyline points="15 3 21 3 21 9"/>'
    '<line x1="10" y1="14" x2="21" y2="3"/>'
    "</svg>"
)

_SVG_CHEVRON = (
    '<svg class="dkb-step-sep" width="12" height="12" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>'
)


def build_theme() -> gr.themes.Base:
    """Swiss-minimalist engineering theme (Navy #1E3A5F + Slate #F8FAFC + Emerald #059669)."""
    return gr.themes.Base(
        primary_hue=gr.themes.colors.slate,
        secondary_hue=gr.themes.colors.blue,
        neutral_hue=gr.themes.colors.slate,
        radius_size=gr.themes.sizes.radius_md,
        spacing_size=gr.themes.sizes.spacing_md,
        font=(
            gr.themes.Font("Outfit"),
            gr.themes.Font("Work Sans"),
            gr.themes.Font("PingFang SC"),
            gr.themes.Font("Microsoft YaHei UI"),
            "ui-sans-serif",
            "system-ui",
            "sans-serif",
        ),
        font_mono=(
            gr.themes.Font("JetBrains Mono"),
            gr.themes.Font("Cascadia Code"),
            "ui-monospace",
            "monospace",
        ),
    ).set(
        body_background_fill="#F8FAFC",
        body_background_fill_dark="#0B1120",
        body_text_color="#0F172A",
        body_text_color_dark="#F1F5F9",
        body_text_color_subdued="#475569",
        body_text_color_subdued_dark="#94A3B8",
        block_background_fill="#FFFFFF",
        block_background_fill_dark="#111827",
        block_border_width="1px",
        block_border_color="#E2E8F0",
        block_border_color_dark="#1F2937",
        block_shadow="0 1px 2px rgba(15, 23, 42, 0.04)",
        block_radius="12px",
        input_background_fill="#FFFFFF",
        input_background_fill_dark="#0F172A",
        input_border_color="#CBD5E1",
        input_border_color_dark="#334155",
        input_border_color_focus="#1E3A5F",
        input_border_color_focus_dark="#60A5FA",
        button_primary_background_fill="#1E3A5F",
        button_primary_background_fill_hover="#162C49",
        button_primary_text_color="#FFFFFF",
        button_primary_background_fill_dark="#2563EB",
        button_primary_background_fill_hover_dark="#1D4ED8",
        button_secondary_background_fill="#FFFFFF",
        button_secondary_background_fill_hover="#F1F5F9",
        button_secondary_border_color="#CBD5E1",
        button_secondary_text_color="#0F172A",
        button_secondary_background_fill_dark="#1E293B",
        button_secondary_background_fill_hover_dark="#334155",
        button_secondary_border_color_dark="#334155",
        button_secondary_text_color_dark="#F1F5F9",
    )


APP_CSS = """
/* === Semantic Design Tokens (ui-ux-pro-max: Minimalism & Swiss Style) === */
:root {
    --dkb-primary: #1E3A5F;
    --dkb-primary-hover: #162C49;
    --dkb-primary-soft: #EFF4FA;
    --dkb-secondary: #2563EB;
    --dkb-secondary-soft: #EFF6FF;
    --dkb-accent: #059669;
    --dkb-accent-soft: #ECFDF5;
    --dkb-accent-border: #A7F3D0;
    --dkb-warn: #B45309;
    --dkb-warn-soft: #FFFBEB;
    --dkb-warn-border: #FDE68A;
    --dkb-err: #B91C1C;
    --dkb-err-soft: #FEF2F2;
    --dkb-err-border: #FECACA;
    --dkb-bg: #F8FAFC;
    --dkb-surface: #FFFFFF;
    --dkb-surface-subtle: #F1F5F9;
    --dkb-fg: #0F172A;
    --dkb-fg-muted: #475569;
    --dkb-border: #E2E8F0;
    --dkb-border-strong: #CBD5E1;
    --dkb-ring: #1E3A5F;
    --dkb-shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
    --dkb-shadow-md: 0 4px 12px rgba(15, 23, 42, 0.06);
    --dkb-radius-sm: 8px;
    --dkb-radius-md: 12px;
    --dkb-radius-lg: 14px;
    --dkb-space-xs: 4px;
    --dkb-space-sm: 8px;
    --dkb-space-md: 16px;
    --dkb-space-lg: 24px;
    --dkb-transition: 180ms cubic-bezier(0.2, 0, 0, 1);
}

.dark {
    --dkb-primary: #60A5FA;
    --dkb-primary-hover: #93C5FD;
    --dkb-primary-soft: #172554;
    --dkb-secondary: #60A5FA;
    --dkb-secondary-soft: #1E293B;
    --dkb-accent: #34D399;
    --dkb-accent-soft: #064E3B;
    --dkb-accent-border: #065F46;
    --dkb-warn: #FBBF24;
    --dkb-warn-soft: #451A03;
    --dkb-warn-border: #78350F;
    --dkb-err: #F87171;
    --dkb-err-soft: #450A0A;
    --dkb-err-border: #7F1D1D;
    --dkb-bg: #0B1120;
    --dkb-surface: #111827;
    --dkb-surface-subtle: #1E293B;
    --dkb-fg: #F1F5F9;
    --dkb-fg-muted: #94A3B8;
    --dkb-border: #1F2937;
    --dkb-border-strong: #334155;
    --dkb-ring: #60A5FA;
    --dkb-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.25);
    --dkb-shadow-md: 0 6px 18px rgba(0, 0, 0, 0.35);
}

/* === Base Layout & Focus Discipline === */
.gradio-container {
    max-width: 1400px !important;
    margin: 0 auto !important;
    padding: 16px 24px 28px 24px !important;
    background: var(--dkb-bg) !important;
    color: var(--dkb-fg) !important;
}

.gradio-container a:focus-visible,
.gradio-container button:focus-visible,
.gradio-container input:focus-visible,
.gradio-container textarea:focus-visible,
.gradio-container [tabindex]:focus-visible {
    outline: 2px solid var(--dkb-ring) !important;
    outline-offset: 2px !important;
}

.gradio-container button {
    cursor: pointer !important;
    transition: background-color var(--dkb-transition), border-color var(--dkb-transition), color var(--dkb-transition), box-shadow var(--dkb-transition) !important;
}

/* === Main 2-Column Workspace === */
#dkb-main-row {
    gap: 20px !important;
    align-items: flex-start !important;
}

/* === Top Editorial Header === */
.dkb-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 14px 20px;
    background: var(--dkb-surface);
    border: 1px solid var(--dkb-border);
    border-top: 3px solid var(--dkb-primary);
    border-radius: var(--dkb-radius-md);
    box-shadow: var(--dkb-shadow-sm);
    margin-bottom: 14px;
}

.dkb-brand {
    display: flex;
    align-items: center;
    gap: 14px;
    min-width: 0;
}

.dkb-logo {
    width: 40px;
    height: 40px;
    border-radius: 10px;
    background: var(--dkb-primary);
    color: #FFFFFF;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(30, 58, 95, 0.22);
}

.dkb-logo svg {
    color: #FFFFFF !important;
    stroke: #FFFFFF !important;
}

.dark .dkb-logo {
    background: #2563EB;
    color: #FFFFFF;
}

.dkb-title-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
}

.dkb-title {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--dkb-fg);
    line-height: 1.25;
}

.dkb-brand-tag {
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--dkb-primary);
    background: var(--dkb-primary-soft);
    padding: 2px 7px;
    border-radius: 6px;
    white-space: nowrap;
}

.dkb-subtitle {
    margin: 3px 0 0 0;
    font-size: 13px;
    color: var(--dkb-fg-muted);
    line-height: 1.4;
}

.dkb-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.dkb-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 500;
    padding: 5px 11px;
    border-radius: 999px;
    background: var(--dkb-surface-subtle);
    border: 1px solid var(--dkb-border);
    color: var(--dkb-fg);
    white-space: nowrap;
    line-height: 1.3;
}

.dkb-pill svg {
    color: var(--dkb-primary);
    flex-shrink: 0;
}

/* === Chat Workspace Polish === */
#dkb-chat-column {
    min-width: 0;
}

#dkb-chatbot {
    border: 1px solid var(--dkb-border) !important;
    border-radius: var(--dkb-radius-lg) !important;
    background: var(--dkb-surface) !important;
    box-shadow: var(--dkb-shadow-sm) !important;
}

#dkb-chatbot .message-wrap,
#dkb-chatbot .prose {
    font-size: 14.5px !important;
    line-height: 1.7 !important;
    color: var(--dkb-fg) !important;
}

#dkb-chatbot .prose blockquote {
    margin: 12px 0 4px 0 !important;
    padding: 10px 14px !important;
    border-left: 3px solid var(--dkb-primary) !important;
    background: var(--dkb-surface-subtle) !important;
    border-radius: 0 8px 8px 0 !important;
    color: var(--dkb-fg-muted) !important;
    font-size: 13px !important;
}

#dkb-chatbot .prose code {
    font-family: "JetBrains Mono", "Cascadia Code", monospace !important;
    font-size: 12.5px !important;
    padding: 1px 5px !important;
    border-radius: 5px !important;
    background: var(--dkb-primary-soft) !important;
    color: var(--dkb-primary) !important;
}

/* Example question chips */
.gradio-container .examples-holder,
.gradio-container .gallery {
    gap: 8px !important;
}

.gradio-container .gallery-item,
.gradio-container button.example {
    border: 1px solid var(--dkb-border-strong) !important;
    border-radius: 999px !important;
    background: var(--dkb-surface) !important;
    color: var(--dkb-fg) !important;
    font-size: 12.5px !important;
    font-weight: 500 !important;
    padding: 6px 13px !important;
    min-height: 34px !important;
    white-space: nowrap !important;
    box-shadow: none !important;
}

.gradio-container .gallery-item:hover,
.gradio-container button.example:hover {
    border-color: var(--dkb-primary) !important;
    background: var(--dkb-primary-soft) !important;
    color: var(--dkb-primary) !important;
}

/* === Welcome Empty State (Swiss 3-Card Grid, Seamless inside Chatbot) === */
.dkb-empty {
    max-width: 720px;
    margin: 4px auto 8px auto;
    padding: 12px 16px 6px 16px;
    border-radius: var(--dkb-radius-md);
    border: none;
    background: transparent;
    text-align: left;
}

.dkb-empty-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--dkb-primary);
    margin-bottom: 6px;
}

.dkb-empty h3 {
    margin: 0 0 6px 0;
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.015em;
    color: var(--dkb-fg);
}

.dkb-empty-lead {
    margin: 0 0 16px 0;
    font-size: 13.5px;
    color: var(--dkb-fg-muted);
    line-height: 1.55;
}

.dkb-feature-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
}

.dkb-feature-card {
    background: var(--dkb-bg);
    border: 1px solid var(--dkb-border);
    border-radius: 10px;
    padding: 13px 14px;
}

.dkb-feature-icon {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: var(--dkb-primary-soft);
    color: var(--dkb-primary);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 10px;
}

.dkb-feature-title {
    font-size: 13px;
    font-weight: 700;
    color: var(--dkb-fg);
    margin-bottom: 4px;
}

.dkb-feature-desc {
    font-size: 12px;
    color: var(--dkb-fg-muted);
    line-height: 1.55;
}

/* === Right Sidebar Panel === */
#status-column {
    border: 1px solid var(--dkb-border) !important;
    border-radius: var(--dkb-radius-lg) !important;
    background: var(--dkb-surface) !important;
    padding: 16px !important;
    box-shadow: var(--dkb-shadow-sm) !important;
    min-width: 0;
}

.dkb-sidebar-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 2px 12px 2px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--dkb-border);
    font-size: 13.5px;
    font-weight: 700;
    color: var(--dkb-fg);
}

.dkb-sidebar-sub {
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--dkb-fg-muted);
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
}

.dkb-card {
    background: var(--dkb-bg);
    border: 1px solid var(--dkb-border);
    border-radius: 10px;
    padding: 13px 14px;
    margin-bottom: 10px;
}

.dkb-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 9px;
}

.dkb-card-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--dkb-fg-muted);
    letter-spacing: 0.01em;
}

.dkb-card-label svg {
    color: var(--dkb-primary);
    flex-shrink: 0;
}

/* === Badges (Single-Line nowrap, WCAG Contrast) === */
.dkb-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11.5px;
    font-weight: 600;
    padding: 2px 9px;
    border-radius: 999px;
    line-height: 1.45;
    white-space: nowrap;
    flex-shrink: 0;
}

.dkb-badge-ok { background: var(--dkb-accent-soft); color: #047857; border: 1px solid var(--dkb-accent-border); }
.dkb-badge-warn { background: var(--dkb-warn-soft); color: var(--dkb-warn); border: 1px solid var(--dkb-warn-border); }
.dkb-badge-err { background: var(--dkb-err-soft); color: var(--dkb-err); border: 1px solid var(--dkb-err-border); }
.dkb-badge-info { background: var(--dkb-secondary-soft); color: #1D4ED8; border: 1px solid #BFDBFE; }
.dkb-badge-neutral { background: var(--dkb-surface); color: var(--dkb-fg-muted); border: 1px solid var(--dkb-border-strong); }

.dark .dkb-badge-ok { color: #6EE7B7; }
.dark .dkb-badge-info { color: #93C5FD; border-color: #1E40AF; }

/* === KPI Strip === */
.dkb-kpi-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin-top: 2px;
}

.dkb-kpi {
    background: var(--dkb-surface);
    border: 1px solid var(--dkb-border);
    border-radius: 8px;
    padding: 9px 10px;
}

.dkb-kpi-val {
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-size: 14px;
    font-weight: 700;
    color: var(--dkb-fg);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

.dkb-kpi-sub {
    font-size: 11px;
    color: var(--dkb-fg-muted);
    margin-top: 2px;
}

.dkb-meta-line {
    font-size: 12px;
    color: var(--dkb-fg-muted);
    margin-top: 8px;
    line-height: 1.55;
}

.dkb-meta-line strong {
    color: var(--dkb-fg);
    font-weight: 600;
}

/* === Source Citation Cards === */
.dkb-source-item {
    padding: 9px 11px;
    border: 1px solid var(--dkb-border);
    border-radius: 8px;
    background: var(--dkb-surface);
    margin-bottom: 7px;
    transition: border-color var(--dkb-transition), box-shadow var(--dkb-transition);
}

.dkb-source-item:last-child { margin-bottom: 0; }

.dkb-source-item:hover {
    border-color: var(--dkb-border-strong);
    box-shadow: var(--dkb-shadow-sm);
}

.dkb-source-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
}

.dkb-source-id {
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-size: 11.5px;
    font-weight: 700;
    color: var(--dkb-primary);
    background: var(--dkb-primary-soft);
    padding: 1px 6px;
    border-radius: 5px;
    white-space: nowrap;
}

.dkb-source-title {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--dkb-fg);
    line-height: 1.45;
    word-break: break-word;
}

.dkb-source-title a {
    color: var(--dkb-secondary);
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

.dkb-source-title a:hover {
    text-decoration: underline;
}

.dkb-ext-svg {
    flex-shrink: 0;
    opacity: 0.85;
}

.dkb-source-path {
    font-size: 11px;
    color: var(--dkb-fg-muted);
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    word-break: break-all;
    margin-top: 3px;
    line-height: 1.35;
}

/* === Connected Execution Timeline === */
.dkb-timeline {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    margin-top: 8px;
}

.dkb-step {
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 6px;
    background: var(--dkb-surface);
    border: 1px solid var(--dkb-border);
    color: var(--dkb-fg);
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

.dkb-step-sep {
    color: var(--dkb-fg-muted);
    opacity: 0.65;
    flex-shrink: 0;
}

.dkb-issue-list {
    margin: 8px 0 0 0;
    padding-left: 16px;
    font-size: 12px;
    color: var(--dkb-warn);
    line-height: 1.5;
}

/* === Bottom Feedback Bar === */
#feedback-bar {
    margin-top: 12px !important;
    padding: 12px 16px !important;
    border: 1px solid var(--dkb-border) !important;
    border-radius: var(--dkb-radius-md) !important;
    background: var(--dkb-surface) !important;
    box-shadow: var(--dkb-shadow-sm) !important;
}

#feedback-bar > div,
#feedback-bar .dkb-fb-row,
#feedback-bar .styler,
#feedback-bar .form {
    align-items: center !important;
    gap: 10px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}

#feedback-bar .dkb-fb-row {
    justify-content: space-between !important;
    width: 100% !important;
}

#feedback-bar .dkb-fb-prompt {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px 0 !important;
    flex: 1 1 auto !important;
    min-width: 180px !important;
}

#feedback-bar .prose p {
    margin: 0 !important;
    font-size: 13px !important;
    color: var(--dkb-fg-muted) !important;
}

#feedback-bar button {
    min-height: 36px !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    border: 1px solid var(--dkb-border-strong) !important;
    background: var(--dkb-surface) !important;
    color: var(--dkb-fg) !important;
}

#feedback-bar button:hover {
    border-color: var(--dkb-primary) !important;
    background: var(--dkb-primary-soft) !important;
    color: var(--dkb-primary) !important;
}

#down-panel {
    margin-top: 12px !important;
    padding: 14px !important;
    border-radius: 10px !important;
    background: var(--dkb-bg) !important;
    border: 1px solid var(--dkb-border) !important;
}

/* === Responsive Breakpoints (375px / 768px / 1024px / 1440px) === */
@media (max-width: 960px) {
    html, body {
        overflow-x: hidden !important;
    }
    .gradio-container {
        padding: 10px 8px 20px 8px !important;
        max-width: 100vw !important;
        overflow-x: hidden !important;
        box-sizing: border-box !important;
    }
    .gradio-container .main,
    .gradio-container .wrap {
        padding-left: 4px !important;
        padding-right: 4px !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
    }
    #dkb-main-row {
        flex-direction: column !important;
        gap: 14px !important;
    }
    #dkb-chat-column,
    #status-column {
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
    }
    #dkb-chatbot {
        height: auto !important;
        min-height: 440px !important;
    }
    .dkb-feature-grid {
        grid-template-columns: 1fr !important;
    }
    .gradio-container .gallery-item,
    .gradio-container button.example {
        white-space: normal !important;
        max-width: 100% !important;
    }
}

@media (max-width: 640px) {
    .dkb-header {
        padding: 12px 14px;
    }
    #feedback-bar .dkb-fb-row {
        flex-wrap: wrap !important;
    }
    #feedback-bar .dkb-fb-prompt {
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 0 !important;
    }
    .dkb-kpi-grid {
        grid-template-columns: 1fr;
    }
}

/* === Reduced Motion Support === */
@media (prefers-reduced-motion: reduce) {
    .gradio-container *,
    .gradio-container *::before,
    .gradio-container *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }
}
"""


SIDEBAR_HEADER_HTML = """
<div class="dkb-sidebar-title">
  <span>参考来源与运行状态</span>
  <span class="dkb-sidebar-sub">WORKSPACE</span>
</div>
"""


HEADER_HTML = f"""
<header class="dkb-header" role="banner">
  <div class="dkb-brand">
    <div class="dkb-logo">{_SVG_BRAND}</div>
    <div>
      <div class="dkb-title-row">
        <h1 class="dkb-title">设计知识库助手</h1>
        <span class="dkb-brand-tag">DesignKB</span>
      </div>
      <p class="dkb-subtitle">面向材料选型、标准合规、制造工艺与尺寸推导的工程设计问答工作台</p>
    </div>
  </div>
  <div class="dkb-pills" aria-label="核心特性">
    <span class="dkb-pill">{_SVG_BOOK}来源可溯源</span>
    <span class="dkb-pill">{_SVG_LAYERS}原文与参考分明</span>
    <span class="dkb-pill">{_SVG_GLOBE}缺口自动联网补充</span>
  </div>
</header>
"""


CHATBOT_PLACEHOLDER_HTML = f"""
<div class="dkb-empty">
  <div class="dkb-empty-eyebrow">{_SVG_LAYERS_16}ENGINEERING KNOWLEDGE WORKSPACE</div>
  <h3>开始你的设计资料查证</h3>
  <p class="dkb-empty-lead">直接输入工艺参数、材料选型、标准条款或尺寸推导问题，系统将优先检索本地设计知识库并自动补全缺失信息。</p>
  <div class="dkb-feature-grid">
    <div class="dkb-feature-card">
      <div class="dkb-feature-icon">{_SVG_BOOK_16}</div>
      <div class="dkb-feature-title">关键结论可溯源</div>
      <div class="dkb-feature-desc">每条参数与工艺规范均绑定来源编号，右侧实时查看文档出处。</div>
    </div>
    <div class="dkb-feature-card">
      <div class="dkb-feature-icon">{_SVG_LAYERS_16}</div>
      <div class="dkb-feature-title">跨语种工艺检索</div>
      <div class="dkb-feature-desc">支持中文直接查询注塑、钣金、CNC、3D 打印与压铸厂商规范。</div>
    </div>
    <div class="dkb-feature-card">
      <div class="dkb-feature-icon">{_SVG_GLOBE_16}</div>
      <div class="dkb-feature-title">缺口自动联网补充</div>
      <div class="dkb-feature-desc">本地资料不完整时自动联网整合完整解答，并在文末标明核验边界。</div>
    </div>
  </div>
</div>
"""


def _evidence_badge(status: str, source_type: str) -> str:
    norm = str(status or "unknown").lower()
    if source_type == "web":
        return '<span class="dkb-badge dkb-badge-info" data-status="web">网络补充 · 需核验</span>'
    if norm == "direct":
        return '<span class="dkb-badge dkb-badge-ok" data-status="direct">直接依据</span>'
    if norm == "indirect":
        return '<span class="dkb-badge dkb-badge-warn" data-status="indirect">背景参考</span>'
    if norm == "scope_mismatch":
        return '<span class="dkb-badge dkb-badge-err" data-status="scope_mismatch">范围不匹配</span>'
    return f'<span class="dkb-badge dkb-badge-neutral" data-status="{html.escape(norm)}">参考资料</span>'


def render_delivery_card(
    state: dict[str, Any] | None,
    *,
    route_label: str = "等待提问",
    stage_note: str | None = None,
) -> str:
    """Render top sidebar card showing delivery badge, route, and blocking issues."""
    if not state:
        return (
            '<section class="dkb-card" aria-label="回答状态">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_SHIELD}回答状态</span>'
            '<span class="dkb-badge dkb-badge-neutral" role="status" aria-atomic="true">待机</span>'
            "</div>"
            f'<div class="dkb-meta-line">检索路径：{html.escape(route_label)}</div>'
            "</section>"
        )

    if stage_note:
        return (
            '<section class="dkb-card" aria-label="回答状态">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_SHIELD}回答状态</span>'
            '<span class="dkb-badge dkb-badge-info" role="status" aria-atomic="true">执行中</span>'
            "</div>"
            f'<div class="dkb-meta-line"><strong>{html.escape(stage_note)}</strong><br/>'
            f"检索路径：{html.escape(route_label)}</div>"
            "</section>"
        )

    deliverable = state.get("deliverable") is True
    status = str(state.get("delivery_status") or state.get("status") or "unknown")
    if deliverable and status == "completed":
        badge = f'<span class="dkb-badge dkb-badge-ok" role="status" aria-atomic="true">{_SVG_CHECK}可交付</span>'
    else:
        badge = f'<span class="dkb-badge dkb-badge-warn" role="status" aria-atomic="true">{_SVG_ALERT}证据不足 · 已降级</span>'

    issues_html = ""
    issues = state.get("blocking_issues", []) or []
    errors = state.get("errors", []) or []
    items: list[str] = []
    seen: set[str] = set()
    for issue in issues[:5]:
        text = f"{issue.get('status', 'unknown')}：{issue.get('reason', '需要补充证据')}"
        if text not in seen:
            seen.add(text)
            items.append(f"<li>{html.escape(text)}</li>")
    for err in errors[:3]:
        text = str(err)
        if text not in seen:
            seen.add(text)
            items.append(f"<li>{html.escape(text)}</li>")
    if items:
        issues_html = f'<ul class="dkb-issue-list">{"".join(items)}</ul>'

    return (
        '<section class="dkb-card" aria-label="回答状态">'
        '<div class="dkb-card-head">'
        f'<span class="dkb-card-label">{_SVG_SHIELD}回答状态</span>'
        f"{badge}"
        "</div>"
        f'<div class="dkb-meta-line">检索路径：<strong>{html.escape(route_label)}</strong></div>'
        f"{issues_html}"
        "</section>"
    )


def render_cost_card(cost_record: dict[str, Any] | None = None, *, pending: bool = False) -> str:
    """Render compact 3-tile KPI card for latency, tokens, and estimated cost."""
    if pending:
        return (
            '<section class="dkb-card" aria-label="耗时与成本">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_CLOCK}耗时与成本</span>'
            '<span class="dkb-badge dkb-badge-info" role="status" aria-atomic="true">统计中</span>'
            "</div>"
            '<div class="dkb-meta-line">正在统计本轮检索与生成耗时…</div>'
            "</section>"
        )
    if not cost_record:
        return (
            '<section class="dkb-card" aria-label="耗时与成本">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_CLOCK}耗时与成本</span>'
            '<span class="dkb-badge dkb-badge-neutral">等待提问</span>'
            "</div>"
            '<div class="dkb-kpi-grid">'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">总耗时</div></div>'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">估算 Token</div></div>'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">估算费用</div></div>'
            "</div>"
            "</section>"
        )

    cache_hit = bool(cost_record.get("cache_hit", False))
    cache_badge = (
        '<span class="dkb-badge dkb-badge-ok">缓存命中</span>'
        if cache_hit
        else '<span class="dkb-badge dkb-badge-neutral">冷检索</span>'
    )
    total_s = float(cost_record.get("total_latency_seconds", 0.0) or 0.0)
    ret_s = float(cost_record.get("retrieval_seconds", 0.0) or 0.0)
    rerank_s = float(cost_record.get("reranker_seconds", 0.0) or 0.0)
    gen_s = float(cost_record.get("generation_seconds", 0.0) or 0.0)
    tok = int(cost_record.get("total_tokens_est", 0) or 0)
    p_tok = int(cost_record.get("prompt_tokens_est", 0) or 0)
    c_tok = int(cost_record.get("completion_tokens_est", 0) or 0)
    cost_cny = float(cost_record.get("estimated_cost_cny", 0.0) or 0.0)

    return (
        '<section class="dkb-card" aria-label="耗时与成本">'
        '<div class="dkb-card-head">'
        f'<span class="dkb-card-label">{_SVG_CLOCK}耗时与成本（估算）</span>'
        f"{cache_badge}"
        "</div>"
        '<div class="dkb-kpi-grid">'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">{total_s:.1f} s</div><div class="dkb-kpi-sub">总耗时</div></div>'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">{tok:,}</div><div class="dkb-kpi-sub">Token</div></div>'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">¥{cost_cny:.4f}</div><div class="dkb-kpi-sub">估算费用</div></div>'
        "</div>"
        f'<div class="dkb-meta-line">检索 {ret_s:.1f}s · 重排 {rerank_s:.1f}s · 生成 {gen_s:.1f}s ｜ 输入 ~{p_tok:,} / 输出 ~{c_tok:,}</div>'
        "</section>"
    )


def render_sources_card(sources: list[dict[str, Any]], *, loading: bool = False) -> str:
    """Render structured evidence cards with direct/indirect/web pills."""
    count_badge = (
        '<span class="dkb-badge dkb-badge-info" role="status" aria-atomic="true">检索中…</span>'
        if loading and not sources
        else f'<span class="dkb-badge dkb-badge-neutral" role="status" aria-atomic="true">{len(sources)} 条</span>'
    )
    if not sources:
        empty_msg = "正在检索本地知识库，命中后会先在此处展示…" if loading else "提问后将展示本轮引用的本地文档与网络来源。"
        return (
            '<section class="dkb-card" aria-label="参考来源">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_BOOK}参考来源</span>'
            f"{count_badge}"
            "</div>"
            f'<div class="dkb-meta-line">{empty_msg}</div>'
            "</section>"
        )

    rows: list[str] = []
    for source in sources[:10]:
        sid = html.escape(str(source.get("id", "?")))
        title = html.escape(str(source.get("title") or "未知来源"))
        location = str(source.get("location") or "未知来源")
        page = source.get("page")
        page_text = f" · 第 {html.escape(str(page))} 页" if page else ""
        badge = _evidence_badge(
            str(source.get("retrieval_evidence_status", "unknown")),
            str(source.get("type", "local")),
        )
        if source.get("type") == "web" and urlparse(location).scheme in {"http", "https"}:
            safe_url = html.escape(location, quote=True)
            title_html = f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{title}{_SVG_EXTERNAL}</a>'
            path_html = html.escape(location[:84])
        else:
            title_html = f"{title}{page_text}"
            path_html = html.escape(location)
        rows.append(
            '<article class="dkb-source-item">'
            f'<div class="dkb-source-top"><span class="dkb-source-id">[{sid}]</span>{badge}</div>'
            f'<div class="dkb-source-title">{title_html}</div>'
            f'<div class="dkb-source-path">{path_html}</div>'
            "</article>"
        )

    return (
        '<section class="dkb-card" aria-label="参考来源">'
        '<div class="dkb-card-head">'
        f'<span class="dkb-card-label">{_SVG_BOOK}参考来源</span>'
        f"{count_badge}"
        "</div>"
        f'{"".join(rows)}'
        "</section>"
    )


def render_trace_card(state: dict[str, Any] | None) -> str:
    """Render compact execution-trace summary with connected node timeline."""
    if not state or not state.get("execution_trace"):
        return (
            '<section class="dkb-card" aria-label="执行轨迹">'
            '<div class="dkb-card-head">'
            f'<span class="dkb-card-label">{_SVG_ACTIVITY}执行轨迹</span>'
            '<span class="dkb-badge dkb-badge-neutral">尚未运行</span>'
            "</div>"
            '<div class="dkb-meta-line">提问后展示问题类型、节点耗时与支持率。</div>'
            "</section>"
        )

    trace = state.get("execution_trace", []) or []
    qtype = html.escape(str(state.get("question_type", "simple")))
    support = state.get("claim_support_rate", 0.0)
    rewrites = int(state.get("rewrite_attempts", 0) or 0)
    steps = [
        f'<span class="dkb-step">{html.escape(str(item.get("node", "")))} {round(float(item.get("duration_seconds", 0) or 0), 1)}s</span>'
        for item in trace[:10]
    ]
    timeline_html = _SVG_CHEVRON.join(steps)
    return (
        '<section class="dkb-card" aria-label="执行轨迹">'
        '<div class="dkb-card-head">'
        f'<span class="dkb-card-label">{_SVG_ACTIVITY}执行轨迹</span>'
        f'<span class="dkb-badge dkb-badge-neutral">{qtype}</span>'
        "</div>"
        f'<div class="dkb-meta-line">支持率 <strong>{support}</strong> · 重写次数 <strong>{rewrites}</strong></div>'
        f'<div class="dkb-timeline">{timeline_html}</div>'
        "</section>"
    )
