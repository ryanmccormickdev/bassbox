"""Library, category-first: home = grid of All + 9 category tiles (colour, name, count); a category shows its
effects as a paged 2-column grid with full descriptions. Tap an effect to insert it at the chosen position
(header ◀ ▶), then edit it. Paging (footer or vertical swipe) instead of scrolling: one flush per page."""
import art
import core
from art import CARD, CATEGORY, CATEGORY_ORDER, EDGE, TXT, TXT2, TXT3, accent, mini_stomp
from chrome import FOOTER_Y, FooterBar, f_label, f_title, shrink, snackbar, wrap
from core import Widget
from gfx import font, mix
from screens.base import BaseScreen, ctx

TOP = 82
TILE_W, TILE_H, GAP = 147, 106, 8
PER_PAGE = 6
DESC_LINES = 3


class CatTile(Widget):
    gestures = ("tap", "press")

    def __init__(self, lib, key, n, x, y, w, h):
        super().__init__(x, y, w, h)
        self.lib, self.key, self.n = lib, key, n
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        self.lib.select(self.key)

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=mix(CARD, 0xFFFFFF, 0.1) if self.pressed else CARD, stroke=EDGE, lw=1.5)
        if self.key == "all":
            for i, c in enumerate(CATEGORY_ORDER[:6]):
                cv.rrect(x + 10 + (i % 3) * 11, y + 12 + (i // 3) * 15, 9, 13, 2.5, fill=accent(c))
            name = "ALL"
        else:
            c = accent(self.key)
            sample = next((e["id"] for e in self.lib.model.catalog if e["category"] == self.key), "")
            mini_stomp(cv, x + 10, y + 8, 30, h - 16, sample, c, True)
            name = CATEGORY[self.key][1].upper()
            if self.key == "modulation":
                name = "MODULATION"
        f, t = shrink(name, "archivo", (14, 13), 800, 125, w - 58)
        if t.endswith("…"):
            f, t = shrink(name, "archivo", (16, 15, 14), 800, 75, w - 58)
        cv.text(t, x + 50, y + h / 2 - 1, f, TXT)
        cv.text(f"{self.n} effect{'s' if self.n != 1 else ''}", x + 50, y + h / 2 + 16, f_label(12, 600), TXT3)


DESC_W = 240


def desc_lines(e):
    return wrap(e.get("description", ""), f_label(12, 450), DESC_W, DESC_LINES)


def row_h(e):
    return 30 + 14 * len(desc_lines(e)) + 4


class Tile(Widget):
    """Full-width effect row: chip, name, the whole description (up to 3 lines)."""
    gestures = ("tap", "press")

    def __init__(self, lib, eff, x, y):
        super().__init__(x, y, 300, row_h(eff))
        self.lib, self.e = lib, eff
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        self.lib.insert(self.e["id"])

    def draw(self, cv):
        e = self.e
        ac = accent(e["category"])
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=mix(CARD, 0xFFFFFF, 0.1) if self.pressed else CARD, stroke=EDGE, lw=1.5)
        mini_stomp(cv, x + 10, y + 9, 30, min(42, h - 18), e["id"], ac, True)
        cv.text(e["name"].upper(), x + 50, y + 23, f_title(13), TXT)
        if self.lib.cat == "all":
            cv.text(CATEGORY[e["category"]][1].upper(), x + w - 10, y + 23, f_label(12, 700), ac, align="right")
        for i, ln in enumerate(desc_lines(e)):
            cv.text(ln, x + 50, y + 40 + i * 14, f_label(12, 450), TXT2)


class Grid(Widget):
    gestures = ("drag",)

    def __init__(self, lib):
        super().__init__(0, TOP - 4, 320, FOOTER_Y - TOP + 2)
        self.lib = lib

    def on_drag_end(self, g):
        if g.dy < -40:
            self.lib.page_by(+1)
        elif g.dy > 40:
            self.lib.page_by(-1)
        elif g.dx > 70 and self.lib.cat is not None:
            self.lib.select(None)        # swipe right: back to categories


class Library(BaseScreen):
    name = "library"

    def __init__(self):
        super().__init__()
        self.cat = None          # None = category home
        self.page = 0
        self.pages = 1
        self.index = 0
        self.grid = Grid(self)
        self.footer = FooterBar([
            ("PREV", lambda cv, x, y, c: art.chevron(cv, x, y, 14, c, "up", 2.6), lambda: self.page_by(-1)),
            ("1/1", None, lambda: None),
            ("NEXT", lambda cv, x, y, c: art.chevron(cv, x, y, 14, c, "down", 2.6), lambda: self.page_by(+1)),
        ])
        self.b_prev, self.b_page, self.b_next = self.footer.buttons
        self.add(self.grid, self.footer)

    def open(self, index):
        self.index = index
        self.page = 0
        self.cat = None

    def configure_header(self):
        n = len(self.model.chain)
        i = max(0, min(n, self.index))
        self.index = i
        title = "Add pedal" if self.cat is None else ("All effects" if self.cat == "all" else CATEGORY[self.cat][1])
        back = (lambda: self.nav.board()) if self.cat is None else (lambda: self.select(None))
        ctx.header.configure(title=title, kicker=f"POS {i + 1}/{n + 1}", back=back,
                             steppers=(lambda: self.move_pos(-1), lambda: self.move_pos(+1), i > 0, i < n),
                             title_color=TXT if self.cat in (None, "all") else accent(self.cat))

    def move_pos(self, d):
        self.index = max(0, min(len(self.model.chain), self.index + d))
        self.configure_header()

    def on_show(self):
        self.layout()
        super().on_show()

    def effects(self):
        cat = self.model.catalog
        if self.cat == "all":
            order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
            return sorted(cat, key=lambda e: order.get(e["category"], 99))
        return [e for e in cat if e["category"] == self.cat]

    def layout(self):
        self.grid.clear()
        if self.cat is None:
            cats = [c for c in CATEGORY_ORDER if any(e["category"] == c for e in self.model.catalog)]
            keys = ["all"] + cats
            th = 60
            for i, k in enumerate(keys):
                n = len(self.model.catalog) if k == "all" else sum(e["category"] == k for e in self.model.catalog)
                col, row = i % 2, i // 2
                self.grid.add(CatTile(self, k, n, 10 + col * (TILE_W + 6), TOP + 2 + row * (th + 8), TILE_W, th))
            self.footer.visible = False
            self.pages = 1
        else:
            effs = self.effects()
            pages, cur, y = [], [], 0
            for e in effs:                       # pack variable-height rows into pages
                hh = row_h(e) + 6
                if cur and y + hh > FOOTER_Y - TOP - 2:
                    pages.append(cur)
                    cur, y = [], 0
                cur.append(e)
                y += hh
            pages.append(cur)
            self.pages = len(pages)
            self.page = max(0, min(self.pages - 1, self.page))
            y = TOP
            for e in pages[self.page]:
                t = Tile(self, e, 10, y)
                self.grid.add(t)
                y += t.h + 6
            self.footer.visible = self.pages > 1
            self.b_page.set_label(f"{self.page + 1} / {self.pages}")
            self.b_prev.enabled = self.page > 0
            self.b_next.enabled = self.page < self.pages - 1
        core.app.dirty((0, TOP - 6, 320, 480 - TOP + 6))

    def select(self, key):
        self.cat = key
        self.page = 0
        self.layout()
        self.configure_header()

    def page_by(self, d):
        p = max(0, min(self.pages - 1, self.page + d))
        if p != self.page:
            self.page = p
            self.layout()

    def insert(self, typ):
        m = self.model
        if len(m.chain) >= 8:
            snackbar("Chain is full (8 pedals)")
            return
        m.add(typ, self.index, cb=lambda uid: self.nav.edit(uid) if uid is not None else self.nav.board())

    def on_model(self, what):
        super().on_model(what)
        if what == "catalog":
            self.layout()
        if what == "chain":
            self.configure_header()


def overflowing(catalog):
    """Descriptions that don't fit DESC_LINES lines at Inter 12 in a tile (for the report)."""
    f = f_label(12, 450)
    return [e["id"] for e in catalog if len(wrap(e.get("description", ""), f, DESC_W, 99)) > DESC_LINES]
