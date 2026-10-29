"""Reusable presentational building blocks for the Streamlit interface.

Everything here is pure presentation: components receive already computed values
and never call the provider or the file system.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st

from src.models.schemas import (
    FeatureStatus,
    HealthCheck,
    HealthStatus,
    PipelineStageReport,
    RoadmapStage,
    StageStatus,
)

_BADGE_VARIANTS: dict[str, str] = {
    HealthStatus.OK.value: "ok",
    HealthStatus.WARNING.value: "warn",
    HealthStatus.ERROR.value: "err",
    HealthStatus.PENDING.value: "plan",
    FeatureStatus.AVAILABLE.value: "ok",
    FeatureStatus.PLANNED.value: "plan",
}

_CHECK_ICONS: dict[str, str] = {
    HealthStatus.OK.value: "✅",
    HealthStatus.WARNING.value: "⚠️",
    HealthStatus.ERROR.value: "⛔",
    HealthStatus.PENDING.value: "⏳",
}


@dataclass(frozen=True)
class InfoCard:
    """Content of a single dashboard card."""

    icon: str
    title: str
    body: str
    badge_text: str | None = None
    badge_variant: str = "plan"


def badge(text: str, variant: str = "plan") -> str:
    """Return the HTML for an inline status badge."""
    return f'<span class="vm-badge vm-badge--{escape(variant)}">{escape(text)}</span>'


def variant_for(value: HealthStatus | FeatureStatus | str) -> str:
    """Map a status enum (or its value) to a badge variant."""
    key = value.value if isinstance(value, (HealthStatus, FeatureStatus)) else str(value)
    return _BADGE_VARIANTS.get(key, "muted")


def render_badges(pairs: Sequence[tuple[str, str]]) -> None:
    """Render a row of badges as ``(text, variant)`` pairs."""
    html = "".join(badge(text, variant) for text, variant in pairs)
    st.markdown(f'<div class="vm-hero__badges">{html}</div>', unsafe_allow_html=True)


def hero(title: str, tagline: str, body: str, badges: Sequence[tuple[str, str]] = ()) -> None:
    """Render the branded hero panel."""
    from src.ui.styles import HERO_EYEBROW

    badge_html = "".join(badge(text, variant) for text, variant in badges)
    st.markdown(
        f"""
        <div class="vm-hero">
            <div class="vm-hero__eyebrow">{escape(HERO_EYEBROW)}</div>
            <h1>{escape(title)}</h1>
            <p class="vm-tagline">{escape(tagline)}</p>
            <p class="vm-hero__body">{escape(body)}</p>
            <div class="vm-hero__badges">{badge_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str, subtitle: str | None = None) -> None:
    """Render a section heading with an optional explanation."""
    subtitle_html = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    st.markdown(
        f'<div class="vm-section"><h3>{escape(title)}</h3>{subtitle_html}</div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    """Render a highlighted note block."""
    st.markdown(f'<div class="vm-note">{escape(text)}</div>', unsafe_allow_html=True)


def card_html(card: InfoCard) -> str:
    """Return the HTML for a single card."""
    badge_html = (
        badge(card.badge_text, card.badge_variant) if card.badge_text else ""
    )
    return (
        '<div class="vm-card">'
        f'<div class="vm-card__icon">{escape(card.icon)}</div>'
        f'<div class="vm-card__title">{escape(card.title)}</div>'
        f'<div class="vm-card__body">{escape(card.body)}</div>'
        f"{badge_html}"
        "</div>"
    )


def render_cards(cards: Sequence[InfoCard]) -> None:
    """Render cards side by side, wrapping into rows of at most four columns."""
    if not cards:
        return
    columns = st.columns(min(len(cards), 4))
    for index, card in enumerate(cards):
        with columns[index % len(columns)]:
            st.markdown(card_html(card), unsafe_allow_html=True)


def render_key_values(rows: Sequence[tuple[str, str]]) -> None:
    """Render read-only label/value rows (all values are HTML escaped)."""
    html = "".join(
        f'<div class="vm-kv"><span class="vm-kv__label">{escape(label)}</span>'
        f'<span class="vm-kv__value">{escape(value)}</span></div>'
        for label, value in rows
    )
    st.markdown(f"<div>{html}</div>", unsafe_allow_html=True)


def render_steps(stages: Sequence[RoadmapStage], note_text: str | None = None) -> None:
    """Render the development roadmap as a vertical step list."""
    items: list[str] = []
    for stage in stages:
        completed = " vm-step--done" if stage.status == FeatureStatus.AVAILABLE else ""
        status_label = (
            "available" if stage.status == FeatureStatus.AVAILABLE else "planned"
        )
        items.append(
            f'<li class="vm-step{completed}">'
            f'<span class="vm-step__num">{stage.number}</span>'
            f"<div><div class=\"vm-step__title\">{escape(stage.title)} "
            f'<span class="vm-badge vm-badge--{"ok" if completed else "plan"}">'
            f"{escape(status_label)}</span></div>"
            f'<p class="vm-step__body">{escape(stage.summary)}</p>'
            "</div></li>"
        )
    st.markdown(f'<ul class="vm-steps">{"".join(items)}</ul>', unsafe_allow_html=True)
    if note_text:
        note(note_text)


def render_status_banner(
    status: HealthStatus | FeatureStatus,
    message: str,
    title: str | None = None,
) -> None:
    """Render a Streamlit status banner for a health or feature status."""
    key = status.value if isinstance(status, (HealthStatus, FeatureStatus)) else str(status)
    text = f"**{title}** - {message}" if title else message
    if key in (HealthStatus.OK.value, FeatureStatus.AVAILABLE.value):
        st.success(text, icon="✅")
    elif key == HealthStatus.WARNING.value:
        st.warning(text, icon="⚠️")
    elif key == HealthStatus.ERROR.value:
        st.error(text, icon="⛔")
    else:
        st.info(text, icon="⏳")


def render_health_checks(checks: Sequence[HealthCheck]) -> None:
    """Render diagnostic checks with their status and safe detail text."""
    if not checks:
        st.caption("No diagnostics available.")
        return
    rows: list[str] = []
    for check in checks:
        key = check.status.value
        rows.append(
            '<div class="vm-check">'
            f'<span class="vm-check__icon">{_CHECK_ICONS.get(key, "•")}</span>'
            "<div>"
            f'<div class="vm-check__title">{escape(check.label)} '
            f'{badge(check.status.value, variant_for(check.status))}</div>'
            f'<div class="vm-check__body">{escape(check.detail)}</div>'
            "</div></div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


_STAGE_VARIANTS: dict[str, str] = {
    StageStatus.COMPLETED.value: "ok",
    StageStatus.FAILED.value: "err",
    StageStatus.SKIPPED.value: "muted",
}


def render_pipeline_stages(stages: Sequence[PipelineStageReport]) -> None:
    """Render the processing stages of a job with their real outcome."""
    if not stages:
        return
    rows: list[str] = []
    for entry in stages:
        key = entry.status.value
        icon = _CHECK_ICONS.get(
            {
                StageStatus.COMPLETED.value: HealthStatus.OK.value,
                StageStatus.FAILED.value: HealthStatus.ERROR.value,
                StageStatus.SKIPPED.value: HealthStatus.PENDING.value,
            }.get(key, ""),
            "-",
        )
        detail_html = (
            f'<div class="vm-check__body">{escape(entry.detail)}</div>' if entry.detail else ""
        )
        rows.append(
            '<div class="vm-check">'
            f'<span class="vm-check__icon">{icon}</span>'
            "<div>"
            f'<div class="vm-check__title">{escape(entry.stage)} '
            f'{badge(key, _STAGE_VARIANTS.get(key, "muted"))}</div>'
            f"{detail_html}"
            "</div></div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


def render_bullets(items: Sequence[str]) -> None:
    """Render a bullet list of plain (escaped) strings."""
    if not items:
        return
    html = "".join(f"<li>{escape(item)}</li>" for item in items)
    st.markdown(f"<ul>{html}</ul>", unsafe_allow_html=True)
