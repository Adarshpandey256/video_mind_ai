"""Visual identity and custom CSS for the VideoMind AI interface."""

from __future__ import annotations

import streamlit as st

BRAND_NAME = "VideoMind AI"
TAGLINE = "Turn any supported video into knowledge."
HERO_EYEBROW = "AI VIDEO INTELLIGENCE PLATFORM"
HERO_BODY = (
    "Understand lectures, meetings, tutorials, interviews and webinars without "
    "watching the entire recording. VideoMind AI transcribes your media, summarises "
    "the content and answers questions about it."
)

APP_CSS = """
<style>
:root {
    --vm-navy: #0b1220;
    --vm-navy-soft: #141d33;
    --vm-violet: #7c6cf6;
    --vm-blue: #4c7dff;
    --vm-ink: #0f172a;
    --vm-muted: #5b6b8c;
    --vm-line: #e3e8f2;
    --vm-panel: #ffffff;
}

.vm-hero {
    background: linear-gradient(135deg, #0b1220 0%, #1b2450 55%, #3b2f7a 100%);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 18px;
    padding: 1.9rem 2rem 1.7rem 2rem;
    color: #f8fafc;
    margin-bottom: 1.1rem;
}
.vm-hero__eyebrow {
    font-size: 0.72rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #a5b4fc;
    font-weight: 600;
    margin-bottom: 0.45rem;
}
.vm-hero h1 {
    font-size: 2.05rem;
    line-height: 1.15;
    margin: 0 0 0.35rem 0;
    font-weight: 700;
    color: #ffffff;
}
.vm-tagline {
    font-size: 1.08rem;
    color: #c7d2fe;
    margin: 0 0 0.6rem 0;
    font-weight: 500;
}
.vm-hero__body {
    font-size: 0.95rem;
    color: #cbd5e1;
    margin: 0 0 0.9rem 0;
    max-width: 62ch;
    line-height: 1.55;
}
.vm-hero__badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }

.vm-badge {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    border: 1px solid transparent;
    white-space: nowrap;
}
.vm-badge--ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; border-color: rgba(74, 222, 128, 0.45); }
.vm-badge--warn { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border-color: rgba(251, 191, 36, 0.45); }
.vm-badge--err { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border-color: rgba(248, 113, 113, 0.45); }
.vm-badge--plan { background: rgba(124, 108, 246, 0.18); color: #c4b5fd; border-color: rgba(167, 139, 250, 0.45); }
.vm-badge--muted { background: rgba(148, 163, 184, 0.16); color: #cbd5e1; border-color: rgba(148, 163, 184, 0.35); }

/* Elements sitting on the page background inherit Streamlit's theme text colour;
   elements inside our own light panels keep explicit dark text. */
.vm-section { margin: 0.4rem 0 0.55rem 0; }
.vm-section h3 {
    margin: 0;
    font-size: 1.16rem;
    font-weight: 650;
    color: inherit;
}
.vm-section p {
    margin: 0.2rem 0 0 0;
    color: inherit;
    opacity: 0.72;
    font-size: 0.9rem;
}

.vm-card {
    background: var(--vm-panel);
    border: 1px solid var(--vm-line);
    border-radius: 14px;
    padding: 1rem 1.05rem;
    height: 100%;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}
.vm-card__icon { font-size: 1.25rem; margin-bottom: 0.35rem; }
.vm-card__title { font-size: 0.98rem; font-weight: 650; color: var(--vm-ink); margin-bottom: 0.25rem; }
.vm-card__body { font-size: 0.86rem; color: var(--vm-muted); line-height: 1.5; margin-bottom: 0.6rem; }

.vm-kv { display: flex; justify-content: space-between; gap: 1rem; padding: 0.42rem 0; border-bottom: 1px solid rgba(148, 163, 184, 0.28); }
.vm-kv:last-child { border-bottom: none; }
.vm-kv__label { font-size: 0.85rem; color: inherit; opacity: 0.72; font-weight: 550; }
.vm-kv__value { font-size: 0.85rem; color: inherit; text-align: right; word-break: break-word; }

.vm-steps { list-style: none; padding: 0; margin: 0.2rem 0 0 0; }
.vm-step {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    padding: 0.6rem 0.75rem;
    border: 1px solid var(--vm-line);
    border-radius: 12px;
    background: var(--vm-panel);
    margin-bottom: 0.45rem;
}
.vm-step__num {
    flex: 0 0 1.75rem;
    height: 1.75rem;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.82rem;
    font-weight: 700;
    background: rgba(124, 108, 246, 0.14);
    color: var(--vm-violet);
}
.vm-step--done .vm-step__num { background: rgba(34, 197, 94, 0.16); color: #16a34a; }
.vm-step__title { font-size: 0.92rem; font-weight: 650; color: var(--vm-ink); }
.vm-step__body { font-size: 0.83rem; color: var(--vm-muted); margin: 0.15rem 0 0 0; line-height: 1.45; }

.vm-check { display: flex; gap: 0.6rem; align-items: flex-start; padding: 0.5rem 0; border-bottom: 1px solid rgba(148, 163, 184, 0.28); }
.vm-check:last-child { border-bottom: none; }
.vm-check__icon { font-size: 1rem; line-height: 1.4; }
.vm-check__title { font-size: 0.89rem; font-weight: 640; color: inherit; }
.vm-check__body { font-size: 0.83rem; color: inherit; opacity: 0.75; line-height: 1.45; }

.vm-seg {
    display: flex;
    gap: 0.75rem;
    padding: 0.32rem 0;
    border-bottom: 1px dashed rgba(148, 163, 184, 0.28);
}
.vm-seg:last-child { border-bottom: none; }
.vm-seg__time {
    flex: 0 0 4.9rem;
    font-size: 0.78rem;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    color: #a78bfa;
    padding-top: 0.08rem;
}
.vm-seg__text { font-size: 0.87rem; line-height: 1.5; color: inherit; }

.vm-note {
    border-left: 3px solid var(--vm-violet);
    background: rgba(124, 108, 246, 0.12);
    border-radius: 0 10px 10px 0;
    padding: 0.7rem 0.9rem;
    font-size: 0.87rem;
    color: inherit;
    line-height: 1.5;
}
.vm-brand { display: flex; align-items: center; gap: 0.55rem; margin-bottom: 0.15rem; }
.vm-brand__mark { font-size: 1.5rem; }
.vm-brand__name { font-size: 1.08rem; font-weight: 700; color: var(--vm-ink); }
.vm-brand__tag { font-size: 0.78rem; color: var(--vm-muted); margin: 0 0 0.8rem 0; line-height: 1.4; }

section[data-testid="stSidebar"] .vm-brand__name,
section[data-testid="stSidebar"] .vm-brand__tag { color: inherit; }
</style>
"""


def inject_styles() -> None:
    """Inject the application stylesheet once per rendered page."""
    st.markdown(APP_CSS, unsafe_allow_html=True)
