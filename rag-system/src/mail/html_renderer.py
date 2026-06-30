"""
Rendu HTML clair pour les réponses email Luciole.

Compatible Outlook desktop (Word rendering engine) :
- CSS entièrement inline (pas de <style> dans le <head>)
- Layout en <table>, pas de flex/grid
- Pas de border-radius dépendant du moteur
- Polices système (pas de @font-face)
- Couleurs sûres, fond clair
"""
from __future__ import annotations

import html
import os
from typing import Iterable


# ── Palette claire « papier + accent doré Luciole » ─────────────────────────
COLOR_BG          = "#FFFFFF"
COLOR_PAGE        = "#F6F4EE"   # fond légèrement crème
COLOR_BORDER      = "#E2DCC9"   # filets clairs
COLOR_TEXT        = "#1F2330"   # corps de texte
COLOR_MUTED       = "#6B6F7A"   # libellés secondaires
COLOR_HEADING     = "#0F1320"
COLOR_ACCENT      = "#C9A227"   # doré Luciole
COLOR_ACCENT_SOFT = "#FAF3D8"   # doré très clair (fond badges)
COLOR_CARD        = "#FFFFFF"

# Largeur max pour la lisibilité (Outlook gère mal > 700px)
EMAIL_WIDTH_PX = 680


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _short_name(path: str) -> str:
    return os.path.basename(path or "") or path or ""


def _format_response_text(text: str) -> str:
    """Convertit le texte brut de réponse en HTML simple (paragraphes + sauts de ligne)."""
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    out = []
    for p in paragraphs:
        p_html = _esc(p).replace("\n", "<br>")
        out.append(
            f'<p style="margin:0 0 12px 0;font-size:15px;line-height:1.55;color:{COLOR_TEXT};">{p_html}</p>'
        )
    return "\n".join(out)


def _source_card(idx: int, name: str, score: float | None) -> str:
    """Une carte de source (numéro + nom de fichier + score) — colonne d'un tableau."""
    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else ""
    score_row = (
        f'<div style="margin-top:6px;font-size:11px;color:{COLOR_MUTED};font-family:Consolas,Menlo,monospace;">score&nbsp;{_esc(score_str)}</div>'
        if score_str else ""
    )
    return f"""\
<td valign="top" width="33%" style="padding:6px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
         style="background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-radius:6px;">
    <tr>
      <td style="padding:10px 12px;">
        <div style="font-size:13px;font-weight:700;color:{COLOR_HEADING};font-family:Arial,Helvetica,sans-serif;line-height:1.35;">
          <span style="display:inline-block;min-width:18px;height:18px;line-height:18px;padding:0 6px;background:{COLOR_ACCENT_SOFT};color:{COLOR_ACCENT};border-radius:9px;font-size:11px;font-weight:700;text-align:center;margin-right:6px;">{idx}</span>
          <span style="word-break:break-all;">{_esc(_short_name(name))}</span>
        </div>
        {score_row}
      </td>
    </tr>
  </table>
</td>"""


def _render_sources_grid(sources: list[dict]) -> str:
    """Grille 3 colonnes de cartes — Outlook supporte les <table> imbriquées."""
    if not sources:
        return ""

    # Déduplication par nom de fichier en gardant le meilleur score
    seen: dict[str, dict] = {}
    for s in sources:
        name = s.get("file_name") or s.get("file_path") or ""
        if not name:
            continue
        key = _short_name(name)
        score = s.get("score")
        prev = seen.get(key)
        if prev is None or (isinstance(score, (int, float)) and (prev.get("score") or 0) < score):
            seen[key] = {"file_name": key, "score": score}

    unique = list(seen.values())

    # Construire des lignes de 3 cellules
    rows_html = []
    for i in range(0, len(unique), 3):
        chunk = unique[i:i + 3]
        cells = [_source_card(i + j + 1, s["file_name"], s.get("score")) for j, s in enumerate(chunk)]
        # Remplir la dernière ligne pour garder l'alignement
        while len(cells) < 3:
            cells.append('<td width="33%" style="padding:6px;">&nbsp;</td>')
        rows_html.append(f'<tr>{"".join(cells)}</tr>')

    return f"""\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:separate;border-spacing:0;margin:0 -6px;">
  {''.join(rows_html)}
</table>"""


def _passage_block(idx: int, passage: dict) -> str:
    file_name = _short_name(passage.get("file_name") or "")
    score = passage.get("score")
    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else ""
    text = passage.get("text") or ""
    # Tronquer plus court qu'en chat pour le mail
    if len(text) > 600:
        text = text[:600].rstrip() + "…"

    meta_parts = []
    if passage.get("page"):
        meta_parts.append(f"p. {_esc(str(passage['page']))}")
    if passage.get("section"):
        meta_parts.append(_esc(str(passage["section"])))
    meta_str = " · ".join(meta_parts)
    meta_html = (
        f'<span style="font-size:11px;color:{COLOR_MUTED};margin-left:8px;">{meta_str}</span>'
        if meta_str else ""
    )

    return f"""\
<tr>
  <td style="padding:8px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
           style="background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-left:3px solid {COLOR_ACCENT};border-radius:4px;">
      <tr>
        <td style="padding:12px 14px;">
          <div style="font-size:12px;color:{COLOR_HEADING};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;">
            <span style="display:inline-block;min-width:18px;height:18px;line-height:18px;padding:0 5px;background:{COLOR_ACCENT_SOFT};color:{COLOR_ACCENT};border-radius:9px;font-size:11px;font-weight:700;text-align:center;margin-right:6px;">{idx}</span>
            <strong style="color:{COLOR_HEADING};">{_esc(file_name)}</strong>
            {meta_html}
            <span style="float:right;font-family:Consolas,Menlo,monospace;font-size:11px;color:{COLOR_MUTED};">{_esc(score_str)}</span>
          </div>
          <div style="font-size:13px;line-height:1.5;color:{COLOR_TEXT};font-family:Georgia,'Times New Roman',serif;">
            {_esc(text)}
          </div>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _render_passages_section(passages: list[dict], max_passages: int = 8) -> str:
    if not passages:
        return ""
    items = passages[:max_passages]
    rows = "\n".join(_passage_block(i + 1, p) for i, p in enumerate(items))
    extra = ""
    if len(passages) > max_passages:
        extra = (
            f'<tr><td style="padding:6px 4px;font-size:12px;color:{COLOR_MUTED};font-style:italic;">'
            f'+ {len(passages) - max_passages} autres extraits non affichés.'
            f'</td></tr>'
        )
    return f"""\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  {rows}
  {extra}
</table>"""


def render_email_html(
    response_text: str,
    sources: Iterable[dict] | None,
    passages: Iterable[dict] | None,
    signature: str = "Luciole — Assistant documentaire\nRéponse générée par IA, validée par un humain.",
) -> str:
    """
    Génère le HTML d'un email Luciole : réponse + grille de sources + extraits.

    Args:
        response_text: réponse en texte brut du LLM
        sources: liste [{file_name, score, ...}]
        passages: liste [{text, file_name, score, page?, section?}]
        signature: signature en texte brut

    Returns:
        HTML complet (string), prêt à mettre dans MIMEText(..., "html", "utf-8").
    """
    sources_list = list(sources or [])
    passages_list = list(passages or [])

    response_html  = _format_response_text(response_text or "")
    sources_html   = _render_sources_grid(sources_list)
    passages_html  = _render_passages_section(passages_list)

    sources_count  = len({_short_name(s.get("file_name") or s.get("file_path") or "") for s in sources_list if (s.get("file_name") or s.get("file_path"))})
    passages_count = len(passages_list)

    signature_html = (
        '<p style="margin:0;font-size:12px;color:' + COLOR_MUTED + ';line-height:1.5;">'
        + _esc(signature).replace("\n", "<br>")
        + '</p>'
    )

    sources_section = ""
    if sources_html:
        sources_section = f"""
<tr>
  <td style="padding:18px 24px 6px 24px;">
    <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:{COLOR_MUTED};font-family:Arial,Helvetica,sans-serif;font-weight:700;margin-bottom:10px;">
      Sources <span style="display:inline-block;min-width:18px;height:18px;line-height:18px;padding:0 6px;background:{COLOR_ACCENT_SOFT};color:{COLOR_ACCENT};border-radius:9px;font-size:11px;font-weight:700;text-align:center;margin-left:4px;">{sources_count}</span>
    </div>
    {sources_html}
  </td>
</tr>"""

    passages_section = ""
    if passages_html:
        passages_section = f"""
<tr>
  <td style="padding:18px 24px 6px 24px;">
    <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:{COLOR_MUTED};font-family:Arial,Helvetica,sans-serif;font-weight:700;margin-bottom:10px;">
      Passages <span style="display:inline-block;min-width:18px;height:18px;line-height:18px;padding:0 6px;background:{COLOR_ACCENT_SOFT};color:{COLOR_ACCENT};border-radius:9px;font-size:11px;font-weight:700;text-align:center;margin-left:4px;">{passages_count}</span>
    </div>
    {passages_html}
  </td>
</tr>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Réponse Luciole</title>
</head>
<body style="margin:0;padding:0;background:{COLOR_PAGE};font-family:Arial,Helvetica,sans-serif;color:{COLOR_TEXT};">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{COLOR_PAGE};">
  <tr>
    <td align="center" style="padding:24px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{EMAIL_WIDTH_PX}"
             style="max-width:{EMAIL_WIDTH_PX}px;background:{COLOR_BG};border:1px solid {COLOR_BORDER};border-radius:8px;">

        <!-- En-tête -->
        <tr>
          <td style="padding:18px 24px;border-bottom:1px solid {COLOR_BORDER};">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td valign="middle">
                  <span style="font-family:Georgia,'Times New Roman',serif;font-size:20px;font-weight:700;font-style:italic;color:{COLOR_HEADING};">Luciole</span>
                  <span style="font-size:12px;color:{COLOR_MUTED};margin-left:8px;">Assistant documentaire</span>
                </td>
                <td valign="middle" align="right">
                  <span style="display:inline-block;padding:3px 10px;background:{COLOR_ACCENT_SOFT};color:{COLOR_ACCENT};border-radius:11px;font-size:11px;font-weight:700;font-family:Arial,Helvetica,sans-serif;letter-spacing:0.04em;">RÉPONSE IA</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Réponse -->
        <tr>
          <td style="padding:22px 24px 6px 24px;">
            <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:{COLOR_MUTED};font-family:Arial,Helvetica,sans-serif;font-weight:700;margin-bottom:10px;">Réponse</div>
            {response_html}
          </td>
        </tr>
        {sources_section}
        {passages_section}

        <!-- Pied -->
        <tr>
          <td style="padding:18px 24px 22px 24px;border-top:1px solid {COLOR_BORDER};">
            {signature_html}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""
