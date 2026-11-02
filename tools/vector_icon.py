"""Иконки плагинов в виде SVG или Android VectorDrawable (xml) -> безопасный одноцветный SVG для каталога.

Каталог раздаёт иконки со своего домена, поэтому сырой SVG принимать нельзя (в нём бывают скрипты).
sanitize() читает только геометрию (path, circle, ellipse, rect, line, polygon, polyline), заливку и обводку,
всё остальное выбрасывает и пишет новый SVG с нуля. Цвета заменяются на один акцентный: приложение всё равно
красит такие иконки в цвет темы. transform, градиенты, текст, картинки и стили-классы не поддерживаются.
"""
import re
import xml.etree.ElementTree as ET

MAX_BYTES = 64 * 1024
MAX_SHAPES = 300
ACCENT = "#d0bcff"
SHAPES = {"path", "circle", "ellipse", "rect", "line", "polygon", "polyline"}
GROUPS = {"g", "group"}
GEOM = re.compile(r"[0-9eE+\-., \t\r\n MmLlHhVvCcSsQqTtAaZz]*")
NUM = re.compile(r"-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
FIELDS = {
    "path": ("d",),
    "circle": ("cx", "cy", "r"),
    "ellipse": ("cx", "cy", "rx", "ry"),
    "rect": ("x", "y", "width", "height", "rx", "ry"),
    "line": ("x1", "y1", "x2", "y2"),
    "polygon": ("points",),
    "polyline": ("points",),
}


def _local(name):
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _attrs(el):
    return {_local(k): v for k, v in el.attrib.items()}


def _num(v, default=None):
    if v is None:
        return default
    m = NUM.match(str(v).strip())
    return float(m.group(0)) if m else default


def _fmt(x):
    return ("%.4f" % x).rstrip("0").rstrip(".") or "0"


def _is_none(v):
    v = (v or "").strip().lower()
    return v in ("none", "transparent") or (v.startswith("#") and len(v) == 9 and v[1:3] == "00")


def _style(a, parent, android):
    s = dict(parent)
    if android:
        names = {"fillColor": "fill", "strokeColor": "stroke", "strokeWidth": "stroke-width",
                 "strokeLineCap": "stroke-linecap", "strokeLineJoin": "stroke-linejoin", "fillType": "fill-rule"}
        for k, dst in names.items():
            if k in a:
                s[dst] = a[k].strip()
    else:
        names = ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "fill-rule")
        for k in names:
            if k in a:
                s[k] = a[k].strip()
        for part in a.get("style", "").split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                if k.strip() in names:
                    s[k.strip()] = v.strip()
    return s


def _emit(name, a, st, android):
    vals = []
    for f in FIELDS[name]:
        v = a.get("pathData" if (android and f == "d") else f)
        if v is not None:
            v = v.strip()
            if not GEOM.fullmatch(v) or len(v) > 20000:
                raise ValueError(f"недопустимое значение {f}")
            vals.append(f'{f}="{v}"')
    if not vals:
        return None
    fill = st.get("fill")
    fill_on = (not android) if fill is None else not _is_none(fill)
    width = _num(st.get("stroke-width"), 0.0 if android else 1.0)
    stroke = st.get("stroke")
    stroke_on = stroke is not None and not _is_none(stroke) and width > 0
    if not fill_on and not stroke_on:
        return None
    out = [f'fill="{ACCENT if fill_on else "none"}"']
    if stroke_on:
        out += [f'stroke="{ACCENT}"', f'stroke-width="{_fmt(min(width, 1000.0))}"']
        cap = (st.get("stroke-linecap") or "").lower()
        join = (st.get("stroke-linejoin") or "").lower()
        if cap in ("round", "square"):
            out.append(f'stroke-linecap="{cap}"')
        if join in ("round", "bevel"):
            out.append(f'stroke-linejoin="{join}"')
    if (st.get("fill-rule") or "").lower() == "evenodd":
        out.append('fill-rule="evenodd"')
    return f'<{name} {" ".join(vals)} {" ".join(out)}/>'


def sanitize(data):
    """bytes (svg или vector xml) -> bytes чистого SVG. ValueError, если файл не подходит."""
    if len(data) > MAX_BYTES:
        raise ValueError("векторная иконка больше 64 КБ")
    low = data.lower()
    if b"<!entity" in low or b"<!doctype" in low:
        raise ValueError("DOCTYPE и ENTITY в иконке не разрешены")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"иконка: некорректный xml ({e})")
    kind = _local(root.tag).lower()
    if kind not in ("svg", "vector"):
        raise ValueError("иконка: корнем должен быть <svg> или <vector>")
    android = kind == "vector"
    ra = _attrs(root)
    if android:
        minx = miny = 0.0
        w, h = _num(ra.get("viewportWidth")), _num(ra.get("viewportHeight"))
    else:
        vb = [_num(x) for x in re.split(r"[\s,]+", ra.get("viewBox", "").strip()) if x]
        if len(vb) == 4 and None not in vb:
            minx, miny, w, h = vb
        else:
            minx = miny = 0.0
            w, h = _num(ra.get("width")), _num(ra.get("height"))
    if not w or not h or w <= 0 or h <= 0 or w > 10000 or h > 10000:
        raise ValueError("иконка: не удалось определить размер (viewBox / viewportWidth)")

    shapes = []

    def walk(el, parent):
        for ch in el:
            if not isinstance(ch.tag, str):
                continue
            name = _local(ch.tag).lower()
            a = _attrs(ch)
            if name in GROUPS:
                walk(ch, _style(a, parent, android))
            elif name in SHAPES:
                s = _emit(name, a, _style(a, parent, android), android)
                if s:
                    shapes.append(s)
                    if len(shapes) > MAX_SHAPES:
                        raise ValueError("в иконке слишком много фигур")

    walk(root, _style(ra, {}, android))
    if not shapes:
        raise ValueError("в иконке нет ни одной поддерживаемой фигуры (path, circle, rect ...)")
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_fmt(minx)} {_fmt(miny)} {_fmt(w)} {_fmt(h)}">'
    return (head + "".join(shapes) + "</svg>\n").encode("utf-8")
