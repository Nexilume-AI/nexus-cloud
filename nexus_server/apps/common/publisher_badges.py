"""Deterministic, self-contained SVG badges. No network, scripts or user markup."""
from hashlib import sha256
from html import escape
import re
import unicodedata


def label(value, limit=38):
    text = ''.join(c for c in str(value) if not unicodedata.category(c).startswith('C'))
    widths = [2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text]
    if sum(widths) <= limit:
        return text
    result, used = '', 0
    for char, width in zip(text, widths):
        if used + width > limit - 1:
            break
        result += char
        used += width
    return result + '…'


def render_badge(*, identity, name, publisher=False, count=0, style='compact', logo=''):
    seed = sha256(str(identity).encode()).digest()
    hue = 145 if publisher else int.from_bytes(seed[:2], 'big') % 360
    accent = '#e5cc8a' if publisher else f'hsl({hue} 65% 78%)'
    ground = f'hsl({hue} 28% 13%)'
    title = label(name)
    subtitle = f'{count} published Agent' + ('s' if count != 1 else '') if publisher else 'Available on Nexus'
    # Only accept normalized raster bytes supplied by the trusted caller.
    if not re.fullmatch(r'data:image/(?:png|webp|jpeg);base64,[A-Za-z0-9+/=]+', logo):
        logo = ''
    logo_markup = f'<image href="{logo}" x="26" y="21" width="48" height="48" clip-path="url(#mark)"/>' if logo else f'<path d="M50 23 71 44 50 65 29 44Z" fill="none" stroke="{accent}" stroke-width="2"/><path d="M50 31 63 44 50 57 37 44Z" fill="{accent}"/><circle cx="50" cy="44" r="5" fill="{ground}"/>'
    rays = ''.join(f'<circle cx="{18 + i * 9}" cy="{73 + seed[i + 2] % 8}" r="1" fill="{accent}" opacity=".7"/>' for i in range(8))
    shield = f'<g><path d="M50 5 88 21 82 65 Q70 86 50 95 Q30 86 18 65 L12 21Z" fill="url(#metal)"/><path d="M50 10 82 25 77 63 Q66 81 50 89 Q34 81 23 63 L18 25Z" fill="{ground}" stroke="{accent}" stroke-opacity=".5"/>{logo_markup}{rays}</g>'
    width, height = (600, 220) if style == 'card' else (420, 64)
    if style == 'card':
        leaves = ''.join(f'<ellipse cx="{28+i*4}" cy="{70+i*14}" rx="5" ry="10" transform="rotate(-38 {28+i*4} {70+i*14})" fill="url(#metal)"/>' for i in range(6))
        body = f'<g transform="translate(15 10)">{leaves}<g transform="translate(200 0) scale(-1 1)">{leaves}</g></g><g transform="translate(49 38) scale(1.3)">{shield}</g><path d="M38 168 Q115 184 190 168 L190 195 Q115 208 38 195Z" fill="url(#metal)"/><text x="114" y="189" text-anchor="middle" fill="#18271d" font-size="11" letter-spacing="2">{"PUBLISHER" if publisher else "NEXUS AGENT"}</text><text x="225" y="57" fill="{accent}" font-size="10" letter-spacing="3">NEXUS MARKETPLACE</text><text x="225" y="99" fill="#f3eee2" font-size="23">{escape(label(title, 24))}</text><path d="M225 120H548" stroke="{accent}" opacity=".3"/><text x="225" y="152" fill="{accent}" font-size="15">{escape(subtitle)}</text><text x="225" y="185" fill="#a9b8ae" font-size="11">{"Nexus Publisher" if publisher else "Built to be shared."}</text>'
    else:
        body = f'<g transform="translate(9 4) scale(.57)">{shield}</g><text x="79" y="27" fill="#f3eee2" font-size="14">{escape(label(title, 32))}</text><text x="79" y="46" fill="{accent}" font-size="11">{escape("Nexus Publisher · " + subtitle if publisher else subtitle)}</text>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)} — {escape(subtitle)}</title><desc id="desc">Nexus Marketplace publication badge. Not a certification or quality rating.</desc>
<defs><linearGradient id="metal" x2="1" y2="1"><stop stop-color="#9b793e"/><stop offset=".28" stop-color="#f3e3af"/><stop offset=".52" stop-color="{accent}"/><stop offset="1" stop-color="#a17d41"/></linearGradient><linearGradient id="bg" x2="1" y2="1"><stop stop-color="hsl({hue} 27% 21%)"/><stop offset="1" stop-color="{ground}"/></linearGradient><clipPath id="mark"><rect x="26" y="21" width="48" height="48" rx="8"/></clipPath></defs>
<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="10" fill="url(#bg)" stroke="{accent}" stroke-opacity=".65"/>
<g font-family="Arial, sans-serif">{body}</g></svg>'''
