"""Render an evidence crop from the exact uploaded PDF, never a template.

Annotations are drawn on an in-memory document only. Ambiguous locations,
rotated pages and changed files fall back to a document/page label.
"""
import hashlib
from pathlib import Path
import pymupdf


def location_label(location):
    return f"{Path(location['document']).stem} · p. {location['page']}"


def compact_locations(locations):
    groups = {}
    for location in locations:
        groups.setdefault(location['document'], set()).add(location['page'])
    return '  |  '.join(f"{Path(name).stem} · {'p.' if len(pages) == 1 else 'pp.'} {', '.join(map(str, sorted(pages)))}"
                       for name, pages in groups.items())


def snapshot(data, location):
    if hashlib.sha256(data).hexdigest() != location['sha256']:
        return None
    try:
        with pymupdf.open(stream=data, filetype='pdf') as document:
            index = location['page'] - 1
            if index < 0 or index >= len(document):
                return None
            page = document[index]
            if page.rotation:
                return None
            if location.get('field'):
                matches = [widget.rect for widget in page.widgets() or [] if widget.field_name == location['field']]
            elif location.get('text'):
                matches = page.search_for(location['text'])
                if location.get('all_text_matches'):
                    matches = [rect for rect in matches if not rect.is_empty]
            else:
                return None
            if not matches or (len(matches) != 1 and not location.get('all_text_matches')):
                return None
            targets = [pymupdf.Rect(match) & page.rect for match in matches]
            target = pymupdf.Rect(targets[0])
            for rect in targets[1:]:
                target |= rect
            if target.is_empty or target.is_infinite:
                return None
            # Include nearby labels and enough space for an arrow.
            clip = pymupdf.Rect(max(0, target.x0 - 150), max(0, target.y0 - 45),
                                min(page.rect.width, target.x1 + 80), min(page.rect.height, target.y1 + 40))
            red = (0.82, 0.12, 0.12)
            for rect in targets:
                page.draw_rect(rect + (-2, -2, 2, 2), color=red, width=1.5, overlay=True)
            # Choose a clear margin within the crop for the arrow tail.
            if clip.x1 - target.x1 >= 35:
                tip = pymupdf.Point(target.x1 + 4, (target.y0 + target.y1) / 2)
                tail = pymupdf.Point(tip.x + 32, tip.y - 18)
            else:
                tip = pymupdf.Point(target.x0 - 4, (target.y0 + target.y1) / 2)
                tail = pymupdf.Point(max(clip.x0 + 3, tip.x - 32), tip.y - 18)
            page.draw_line(tail, tip, color=red, width=2, overlay=True)
            vector = tail - tip
            length = (vector.x ** 2 + vector.y ** 2) ** 0.5
            if length:
                ux, uy = vector.x / length, vector.y / length
                for side in [-1, 1]:
                    endpoint = pymupdf.Point(tip.x + 9 * ux + side * 4 * uy,
                                            tip.y + 9 * uy - side * 4 * ux)
                    page.draw_line(tip, endpoint, color=red, width=2, overlay=True)
            return page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, annots=True).tobytes('png')
    except Exception:
        return None
