"""FundLens visual language and small, reusable Streamlit presentation helpers."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

import streamlit as st

INK = "#10221d"
MUTED_INK = "#63706b"
EMERALD = "#087855"
LILAC = "#d9cff0"
CANVAS = "#f5f7f2"


def inject_global_styles() -> None:
    """Apply the FundLens design system without changing Streamlit internals."""
    st.markdown(
        """
        <style>
        :root {
          --fl-ink: #10221d;
          --fl-muted: #63706b;
          --fl-emerald: #087855;
          --fl-emerald-dark: #055c41;
          --fl-mint: #dcefe5;
          --fl-lilac: #d9cff0;
          --fl-lilac-soft: #f0ebfa;
          --fl-paper: #ffffff;
          --fl-canvas: #f5f7f2;
          --fl-line: #dfe5df;
          --fl-warning: #a56012;
          --fl-danger: #a33a3a;
          --fl-body-font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          --fl-display-font: ui-rounded, "Segoe UI", ui-sans-serif, sans-serif;
        }

        .stApp {
          background:
            radial-gradient(circle at 94% 2%, rgba(217, 207, 240, .34), transparent 25rem),
            radial-gradient(circle at 5% 22%, rgba(220, 239, 229, .42), transparent 26rem),
            var(--fl-canvas);
          color: var(--fl-ink);
          font-family: var(--fl-body-font);
        }

        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stToolbar"] { right: 1rem; }
        [data-testid="stAppViewContainer"] > .main { overflow: visible; }
        .block-container {
          max-width: 1320px;
          padding-top: 1.7rem;
          padding-bottom: 5rem;
        }

        h1, h2, h3, h4, [data-testid="stMetricValue"] {
          font-family: var(--fl-display-font) !important;
          color: var(--fl-ink) !important;
          letter-spacing: -0.035em;
        }
        h1 { font-size: clamp(2.35rem, 5vw, 4.5rem) !important; line-height: 1.02 !important; }
        h2 { font-size: clamp(1.55rem, 2.4vw, 2.15rem) !important; }
        h3 { font-size: 1.1rem !important; letter-spacing: -0.02em; }
        p, label, [data-testid="stMarkdownContainer"] { color: var(--fl-ink); }

        .fl-topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 1rem;
          margin-bottom: 1.7rem;
        }
        .fl-wordmark { display: flex; align-items: center; gap: .72rem; }
        .fl-mark {
          display: grid;
          place-items: center;
          width: 2.45rem;
          height: 2.45rem;
          border-radius: .8rem;
          color: white;
          background: var(--fl-ink);
          font: 700 1.05rem/1 var(--fl-display-font);
          box-shadow: inset 0 0 0 1px rgba(255,255,255,.08);
        }
        .fl-brand { font: 700 1.2rem/1 var(--fl-display-font); letter-spacing: -.04em; }
        .fl-brand-note { color: var(--fl-muted); font-size: .77rem; margin-top: .28rem; }
        .fl-session-chip {
          display: inline-flex;
          align-items: center;
          gap: .48rem;
          padding: .48rem .72rem;
          border: 1px solid var(--fl-line);
          border-radius: 999px;
          background: rgba(255,255,255,.72);
          color: var(--fl-muted);
          font-size: .78rem;
          white-space: nowrap;
        }
        .fl-dot { width: .45rem; height: .45rem; border-radius: 50%; background: var(--fl-emerald); }

        .fl-hero {
          position: relative;
          overflow: hidden;
          padding: clamp(1.7rem, 4vw, 3.1rem);
          margin: .6rem 0 1.35rem;
          border: 1px solid rgba(16,34,29,.12);
          border-radius: 1.65rem;
          background: rgba(255,255,255,.82);
          box-shadow: 0 22px 65px rgba(34,58,49,.08);
        }
        .fl-hero::after {
          content: "";
          position: absolute;
          width: 15rem;
          height: 15rem;
          top: -6rem;
          right: -3rem;
          border-radius: 50%;
          background: var(--fl-lilac);
          opacity: .62;
          filter: blur(2px);
        }
        .fl-eyebrow {
          position: relative;
          z-index: 1;
          color: var(--fl-emerald);
          font-size: .74rem;
          font-weight: 700;
          letter-spacing: .12em;
          text-transform: uppercase;
        }
        .fl-hero h1 { position: relative; z-index: 1; max-width: 840px; margin: .72rem 0 .85rem; }
        .fl-hero p { position: relative; z-index: 1; max-width: 680px; color: var(--fl-muted); font-size: 1.02rem; }

        .fl-section-head {
          display: flex;
          justify-content: space-between;
          align-items: end;
          gap: 1rem;
          margin: 2rem 0 1rem;
        }
        .fl-section-head h2 { margin: 0; }
        .fl-section-kicker { color: var(--fl-emerald); font-size: .71rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
        .fl-section-copy { color: var(--fl-muted); max-width: 630px; font-size: .91rem; margin: .35rem 0 0; }

        .fl-card, [data-testid="stForm"] {
          border: 1px solid var(--fl-line);
          border-radius: 1.15rem;
          background: rgba(255,255,255,.9);
          box-shadow: 0 12px 35px rgba(34,58,49,.055);
        }
        .fl-card { padding: 1.15rem; }
        [data-testid="stForm"] { padding: 1.2rem 1.2rem .35rem; }
        [data-testid="stVerticalBlockBorderWrapper"] {
          border-color: var(--fl-line) !important;
          border-radius: 1.1rem !important;
          background: rgba(255,255,255,.76);
          box-shadow: 0 8px 28px rgba(34,58,49,.04);
        }

        .fl-file-card {
          min-height: 7.2rem;
          padding: 1rem;
          border: 1px solid var(--fl-line);
          border-radius: 1rem;
          background: rgba(255,255,255,.9);
        }
        .fl-file-index { color: var(--fl-emerald); font: 700 .72rem/1 var(--fl-display-font); text-transform: uppercase; }
        .fl-file-name { font-weight: 650; margin: .5rem 0 .3rem; overflow-wrap: anywhere; }
        .fl-file-meta { color: var(--fl-muted); font-size: .76rem; }

        .fl-badge {
          display: inline-flex;
          align-items: center;
          padding: .28rem .55rem;
          border-radius: 999px;
          background: var(--fl-lilac-soft);
          color: #544174;
          font-size: .72rem;
          font-weight: 650;
          line-height: 1;
        }
        .fl-badge--green { background: var(--fl-mint); color: #075e43; }
        .fl-badge--amber { background: #faead3; color: #8b5111; }
        .fl-badge--red { background: #f7dddd; color: #903333; }
        .fl-badge--neutral { background: #edf0ed; color: #52605a; }
        .fl-muted { color: var(--fl-muted); }

        .fl-callout {
          padding: .9rem 1rem;
          border-left: 3px solid var(--fl-lilac);
          border-radius: .2rem .85rem .85rem .2rem;
          background: rgba(240,235,250,.7);
          color: #4d435d;
          font-size: .86rem;
        }
        .fl-callout--warning { border-color: #e5a34e; background: #fff5e7; color: #724814; }
        .fl-callout--danger { border-color: #c45a5a; background: #fff0f0; color: #773131; }

        [data-testid="stFileUploaderDropzone"] {
          min-height: 11rem;
          border: 1.5px dashed #aebbb4;
          border-radius: 1.2rem;
          background: rgba(255,255,255,.65);
        }
        [data-testid="stFileUploaderDropzone"]:hover { border-color: var(--fl-emerald); background: rgba(220,239,229,.25); }
        [data-testid="stFileUploaderDropzoneInstructions"] span { font-size: .96rem; }

        .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button {
          min-height: 2.75rem;
          border-radius: .78rem;
          border: 1px solid #cdd7d1;
          font-weight: 650;
          transition: transform .12s ease, box-shadow .12s ease, background .12s ease;
        }
        .stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] button:hover {
          border-color: var(--fl-emerald);
          color: var(--fl-emerald);
          transform: translateY(-1px);
          box-shadow: 0 8px 20px rgba(8,120,85,.11);
        }
        .stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {
          color: #fff;
          border-color: var(--fl-emerald);
          background: var(--fl-emerald);
        }
        .stButton > button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primary"]:hover {
          color: #fff;
          background: var(--fl-emerald-dark);
        }
        [data-baseweb="input"] > div, [data-baseweb="textarea"] > div, [data-baseweb="select"] > div {
          border-color: #cfd8d2 !important;
          border-radius: .75rem !important;
          background: rgba(255,255,255,.92) !important;
        }
        [data-baseweb="tab-list"] {
          gap: .25rem;
          padding: .28rem;
          border: 1px solid var(--fl-line);
          border-radius: .9rem;
          background: rgba(255,255,255,.74);
        }
        [data-baseweb="tab"] { border-radius: .65rem; padding: .5rem .8rem; }
        [aria-selected="true"][data-baseweb="tab"] { background: var(--fl-ink); color: white; }
        [data-testid="stDataFrame"] { border: 1px solid var(--fl-line); border-radius: 1rem; overflow: hidden; }
        [data-testid="stMetric"] {
          padding: 1rem;
          border: 1px solid var(--fl-line);
          border-radius: 1rem;
          background: rgba(255,255,255,.86);
        }
        [data-testid="stMetricLabel"] { color: var(--fl-muted); }
        hr { border-color: var(--fl-line); }

        @media (max-width: 720px) {
          .block-container { padding: 1rem .9rem 4rem; }
          .fl-topbar { align-items: flex-start; }
          .fl-brand-note { display: none; }
          .fl-hero { border-radius: 1.2rem; }
          .fl-section-head { display: block; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_topbar(*, api_key_present: bool) -> None:
    """Render the brand header and a non-sensitive session status."""
    status = "AI session ready" if api_key_present else "Local session only"
    st.markdown(
        f"""
        <div class="fl-topbar">
          <div class="fl-wordmark">
            <div class="fl-mark">FL</div>
            <div>
              <div class="fl-brand">FundLens</div>
              <div class="fl-brand-note">Evidence-grounded fund research</div>
            </div>
          </div>
          <div class="fl-session-chip"><span class="fl-dot"></span>{escape(status)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero() -> None:
    """Render the product proposition at the start of the workflow."""
    st.markdown(
        """
        <section class="fl-hero">
          <div class="fl-eyebrow">Research workspace · private by design</div>
          <h1>See the evidence.<br>Compare with confidence.</h1>
          <p>Turn issuer factsheets and reproducible price data into a reviewable,
          evidence-checked comparison brief—without turning research into a recommendation.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def section_header(kicker: str, title: str, description: str) -> None:
    """Render a consistently spaced section introduction."""
    st.markdown(
        f"""
        <div class="fl-section-head">
          <div>
            <div class="fl-section-kicker">{escape(kicker)}</div>
            <h2>{escape(title)}</h2>
            <p class="fl-section-copy">{escape(description)}</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def badge(label: str, tone: str = "neutral") -> str:
    """Return escaped badge markup for trusted, application-owned labels."""
    supported_tones = {"green", "amber", "red", "neutral", "lilac"}
    selected_tone = tone if tone in supported_tones else "neutral"
    tone_class = "" if selected_tone == "lilac" else f" fl-badge--{selected_tone}"
    return f'<span class="fl-badge{tone_class}">{escape(label)}</span>'


def callout(message: str, tone: str = "info") -> None:
    """Render an escaped contextual notice."""
    class_suffix = "" if tone == "info" else f" fl-callout--{tone}"
    st.markdown(
        f'<div class="fl-callout{class_suffix}">{escape(message)}</div>',
        unsafe_allow_html=True,
    )


def render_step_strip(steps: Sequence[str], active_index: int, completed_index: int) -> None:
    """Render a compact, non-interactive workflow progress strip."""
    cells: list[str] = []
    for index, label in enumerate(steps):
        if index < active_index or index <= completed_index:
            tone = "green"
            symbol = "✓"
        elif index == active_index:
            tone = "lilac"
            symbol = str(index + 1)
        else:
            tone = "neutral"
            symbol = str(index + 1)
        cells.append(
            f'<div style="display:flex;align-items:center;gap:.42rem;white-space:nowrap">'
            f'{badge(symbol, tone)}<span style="font-size:.78rem;color:{MUTED_INK}">{escape(label)}</span></div>'
        )
    st.markdown(
        '<div style="display:flex;gap:1.05rem;align-items:center;overflow-x:auto;'
        'padding:.7rem .1rem 1.05rem">' + "".join(cells) + "</div>",
        unsafe_allow_html=True,
    )
