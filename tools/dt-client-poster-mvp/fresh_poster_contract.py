"""Bounded wire and visual checks for the new CN poster interaction."""
import re

SELL = "dt_cn_poster_selling_point"
LAYOUT = "dt_cn_poster_layout_skill"
DETAIL = "dt_cn_main_and_long_detail_skill"
VISUAL_SECTIONS = ("fit", "quality", "spec", "use")
MAIN_CARDS = ("feature", "advantage", "benefit", "proof", "scene")
SCOPE = "customer_token_plan_new_poster"
WIDTH, HEIGHT = 720, 880
LAYERS = ("B01", "M01", "I01", "T02", "T01")
TOKEN = re.compile(r"[0-9a-f]{32}")
HASH = re.compile(r"[0-9a-f]{64}")


def overlaps(a, b):
    return (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])


def luminance(color):
    values = [int(color[i:i+2], 16)/255 for i in (1,3,5)]
    linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
    return sum(k*v for k,v in zip((.2126,.7152,.0722),linear,strict=True))


def readable(objects):
    # Text and product areas are independent. Where text covers the decorative card,
    # the glyph color must remain readable against every backing color touched.
    title, subtitle = objects["T01"], objects["T02"]
    photo, card, canvas = (objects[k] for k in ("I01","M01","B01"))
    if overlaps(title,subtitle) or overlaps(photo,title) or overlaps(photo,subtitle):
        raise ValueError("layout_text_obstruction")
    for text in (title,subtitle):
        inside = (card["x"] <= text["x"] and text["x"] + text["width"] <= card["x"] + card["width"]
                  and card["y"] <= text["y"] and text["y"] + text["height"] <= card["y"] + card["height"])
        backgrounds = [card] if inside else [canvas,card] if overlaps(text,card) else [canvas]
        for background in backgrounds:
            a,b = sorted((luminance(text["fill"]),luminance(background["fill"])))
            if (b+.05)/(a+.05) < 4.5:
                raise ValueError("layout_low_contrast")
