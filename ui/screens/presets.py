"""Presets: FACTORY / USER sections, tap to load, Save (overwrite user) / Save As (name generator).
Long-press a user preset to delete it."""
import random

import art
import core
from art import CARD, EDGE, TXT, TXT2, TXT3
from chrome import FOOTER_Y, Button, Dialog, FooterBar, f_label, f_num, f_title, fit, snackbar
from core import Widget
from gfx import font, mix, text_width
from screens.base import BaseScreen, ctx

LIST_TOP, LIST_BOT = 80, 428
ROW_H, SEC_H = 54, 28

ADJ = ["Velvet", "Rusty", "Cosmic", "Deep", "Molten", "Feral", "Midnight", "Analog", "Thick", "Lunar", "Woolly",
       "Electric", "Gritty", "Golden", "Murky", "Neon", "Smoky", "Tectonic", "Hollow", "Savage", "Liquid", "Dusty",
       "Granite", "Frozen", "Burnt", "Heavy", "Wobbly", "Stellar", "Swampy", "Chrome", "Crimson", "Low-Slung",
       "Sticky", "Vintage", "Brutal", "Silky", "Static", "Magnetic", "Phantom", "Rubber"]
NOUN = ["Mammoth", "Rumble", "Groove", "Thunder", "Basement", "Walrus", "Engine", "Swamp", "Canyon", "Tremor",
        "Leviathan", "Bison", "Monolith", "Subway", "Avalanche", "Ghost", "Furnace", "Glacier", "Pendulum", "Comet",
        "Riff", "Bulldozer", "Octopus", "Earthquake", "Tugboat", "Gorilla", "Volcano", "Freight", "Mastodon",
        "Lighthouse", "Cathedral", "Sandstorm", "Hammer", "Tide", "Robot", "Moose", "Reactor", "Undertow"]


def gen_name(taken=(), rng=random):
    for _ in range(50):
        n = f"{rng.choice(ADJ)} {rng.choice(NOUN)}"
        if n not in taken:
            return n
    return n


class Row(Widget):
    gestures = ("tap", "long", "press")

    def __init__(self, scr, preset, y):
        super().__init__(10, y, 300, ROW_H - 4)
        self.scr, self.p = scr, preset
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        self.scr.load(self.p["name"])

    def on_long_press(self, g):
        if not self.p.get("factory"):
            self.scr.ask_delete(self.p["name"])

    def draw(self, cv):
        m = self.scr.model
        cur = self.p["name"] == m.state.get("name")
        x, y, w, h = self.rect
        fill = mix(CARD, 0xFFFFFF, 0.1) if self.pressed else (0x262B33 if cur else CARD)
        cv.rrect(x, y, w, h, 10, fill=fill, stroke=TXT if cur else EDGE, lw=2.2 if cur else 1.5)
        if cur:
            cv.rrect(x, y + 6, 5, h - 12, 2.5, fill=art.GREEN if not m.state.get("dirty") else 0xFF8A00)
        cv.text(fit(self.p["name"], font("inter", 16, 650), 196), x + 16, y + 23, font("inter", 16, 650), TXT)
        chain = m.preset_chain(self.p["name"])
        if chain is not None:
            if not chain:
                cv.text("empty chain", x + 16, y + 41, f_label(12, 500), TXT3)
            for i, (typ, en) in enumerate(chain[:12]):
                c = art.accent(m.info(typ)["category"])
                cx = x + 21 + i * 14
                if en:
                    cv.circle(cx, y + 37, 4.5, fill=c)
                else:
                    cv.circle(cx, y + 37, 4, stroke=c, lw=1.5)
        if cur:
            tag, col = ("EDITED", 0xFF8A00) if m.state.get("dirty") else ("LOADED", art.GREEN)
            cv.text(tag, x + w - 14, y + 23, f_label(12, 800), col, align="right", tracking=0.8)


class Section(Widget):
    def __init__(self, label, y, count):
        super().__init__(10, y, 300, SEC_H)
        self.label, self.count = label, count

    def draw(self, cv):
        cv.text(self.label, self.x + 6, self.y + 19, f_label(12, 750), TXT3, tracking=1.6)
        cv.text(str(self.count), self.x + self.w - 6, self.y + 19, f_num(12, 600), TXT3, align="right")
        cv.rect(self.x + 6 + text_width(self.label, f_label(12, 750), tracking=1.6) + 10, self.y + 14, 200, 1, fill=0x2C3038)


class ListArea(Widget):
    gestures = ("drag",)

    def __init__(self, scr):
        super().__init__(0, LIST_TOP, 320, LIST_BOT - LIST_TOP)
        self.scr = scr

    def on_drag_end(self, g):
        if g.dy < -40:
            self.scr.page_by(+1)
        elif g.dy > 40:
            self.scr.page_by(-1)


class Presets(BaseScreen):
    name = "presets"

    def __init__(self):
        super().__init__()
        self.page = 0
        self.pages = [[]]
        self.list = ListArea(self)
        self.footer = FooterBar([
            ("SAVE", lambda cv, x, y, c: art.check(cv, x, y, 14, c), self.save),
            ("SAVE AS", lambda cv, x, y, c: art.plus(cv, x, y, 13, c), lambda: self.nav.save_as()),
            ("PREV", lambda cv, x, y, c: art.chevron(cv, x, y, 14, c, "up", 2.6), lambda: self.page_by(-1)),
            ("NEXT", lambda cv, x, y, c: art.chevron(cv, x, y, 14, c, "down", 2.6), lambda: self.page_by(+1)),
        ])
        self.add(self.list, self.footer)

    def configure_header(self):
        n = len(self.pages)
        ctx.header.configure(title="Presets", kicker=f"PAGE {self.page + 1}/{n}" if n > 1 else "LOAD · SAVE",
                             back=lambda: self.nav.board())

    def on_show(self):
        self.model.refresh_presets()
        self.layout()
        super().on_show()

    def items(self):
        ps = self.model.presets
        fac = [p for p in ps if p.get("factory")]
        usr = [p for p in ps if not p.get("factory")]
        out = [("sec", "FACTORY", len(fac))] + [("p", p) for p in fac]
        out += [("sec", "USER", len(usr))] + ([("p", p) for p in usr] if usr else [("empty",)])
        return out

    def layout(self):
        pages, cur, y = [], [], 0
        avail = LIST_BOT - LIST_TOP
        for it in self.items():
            hh = SEC_H if it[0] == "sec" else ROW_H
            if y + hh > avail or (it[0] == "sec" and y + SEC_H + ROW_H > avail and cur):
                pages.append(cur)
                cur, y = [], 0
            cur.append(it)
            y += hh
        pages.append(cur)
        self.pages = pages
        self.page = max(0, min(len(pages) - 1, self.page))
        self.list.clear()
        y = LIST_TOP
        for it in pages[self.page]:
            if it[0] == "sec":
                self.list.add(Section(it[1], y, it[2]))
                y += SEC_H
            elif it[0] == "p":
                self.list.add(Row(self, it[1], y))
                y += ROW_H
            else:
                self.list.add(EmptyNote(y))
                y += ROW_H
        b = self.footer.buttons
        b[2].enabled = self.page > 0
        b[3].enabled = self.page < len(pages) - 1
        core.app.dirty((0, LIST_TOP - 6, 320, 480 - LIST_TOP + 6))
        if self.attached():
            self.configure_header()

    def page_by(self, d):
        p = max(0, min(len(self.pages) - 1, self.page + d))
        if p != self.page:
            self.page = p
            self.layout()

    def load(self, name):
        self.model.load_preset(name, cb=lambda r: snackbar(f"Loaded {name}") if r.get("ok") else snackbar("Load failed"))

    def save(self):
        m = self.model
        if not m.state.get("name") or m.current_is_factory():
            self.nav.save_as()
            return
        m.save_preset(None, cb=lambda r: snackbar(f"Saved {m.state.get('name')}") if r.get("ok") else snackbar("Save failed"))

    def ask_delete(self, name):
        core.app.add_overlay(Dialog("Delete preset?", f"“{name}” will be removed. Factory presets stay.", "Delete",
                                    lambda: self.model.delete_preset(name, cb=lambda r: snackbar(f"Deleted {name}")),
                                    danger=True))

    def on_model(self, what):
        super().on_model(what)
        if what in ("presets", "chain"):
            self.layout()


class EmptyNote(Widget):
    def __init__(self, y):
        super().__init__(10, y, 300, ROW_H - 4)

    def draw(self, cv):
        cv.rrect(self.x, self.y, self.w, self.h, 10, stroke=0x3A3E46, lw=1.5)
        cv.text("No user presets yet — use SAVE AS", 160, self.y + 26, f_label(13, 500), TXT3, align="center")


# ====================================================================== save as
class WordButton(Widget):
    gestures = ("tap", "press")

    def __init__(self, scr, which, y):
        super().__init__(20, y, 280, 56)
        self.scr, self.which = scr, which
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        self.scr.reroll(self.which)

    def draw(self, cv):
        word = self.scr.words[self.which].upper()
        size = 26
        while size > 14 and text_width(word, f_title(size)) > self.w - 20:
            size -= 1
        if self.pressed:
            cv.rrect(self.x, self.y, self.w, self.h, 12, fill=0xFFFFFF, alpha=0.08)
        cv.text(word, 160, self.y + 40, f_title(size), TXT, align="center")


class SaveAs(BaseScreen):
    name = "saveas"

    def __init__(self):
        super().__init__()
        self.words = ["Velvet", "Mammoth"]
        self.card = Widget(14, 92, 292, 196)
        self.w_adj = WordButton(self, 0, 124)
        self.w_noun = WordButton(self, 1, 180)
        self.b_shuffle = Button(70, 304, 180, 52, "SHUFFLE", self.shuffle, "outline", TXT,
                                icon=lambda cv, x, y, c: art.shuffle_icon(cv, x, y, 18, c), font_=f_title(14))
        self.footer = FooterBar([
            ("CANCEL", lambda cv, x, y, c: art.cross(cv, x, y, 13, c), lambda: self.nav.presets()),
            ("SAVE", lambda cv, x, y, c: art.check(cv, x, y, 16, c), self.save, art.GREEN),
        ])
        self.add(CardBg(self), self.w_adj, self.w_noun, self.b_shuffle, Hint(), self.footer)

    def configure_header(self):
        ctx.header.configure(title="Save as", kicker="NEW USER PRESET", back=lambda: self.nav.presets())

    def on_show(self):
        self.shuffle(redraw=False)
        super().on_show()

    def taken(self):
        return {p["name"] for p in self.model.presets}

    def shuffle(self, redraw=True):
        n = gen_name(self.taken())
        self.words = n.split(" ", 1)
        self.w_adj.invalidate()
        self.w_noun.invalidate()

    def reroll(self, which):
        pool = ADJ if which == 0 else NOUN
        cur = self.words[which]
        self.words[which] = random.choice([w for w in pool if w != cur])
        (self.w_adj if which == 0 else self.w_noun).invalidate()

    @property
    def name_str(self):
        return " ".join(self.words)

    def save(self):
        name = self.name_str
        self.model.save_preset(name, cb=lambda r: (snackbar(f"Saved {name}") if r.get("ok") else snackbar("Save failed")))
        self.nav.board()


class CardBg(Widget):
    def __init__(self, scr):
        super().__init__(14, 92, 292, 196)
        self.scr = scr

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 16, fill=0x14161A, stroke=EDGE, lw=1.5)
        cv.text("PRESET NAME", 160, y + 24, f_label(12, 750), TXT3, align="center", tracking=1.6)
        cv.rect(x + 40, y + 148, w - 80, 1, fill=0x2C3038)
        cv.text("tap a word to change just that word", 160, y + 176, f_label(13, 500), TXT2, align="center")


class Hint(Widget):
    def __init__(self):
        super().__init__(0, 370, 320, 40)

    def draw(self, cv):
        cv.text("Saved as a user preset; factory presets", 160, 384, f_label(12, 500), TXT3, align="center")
        cv.text("are never overwritten.", 160, 400, f_label(12, 500), TXT3, align="center")
