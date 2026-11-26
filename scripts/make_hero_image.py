"""Generate a stacked-screenshots hero image for the Solea README."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
import math
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(BASE, "frontend", "public", "screenshots")
OUT = os.path.join(SHOTS, "hero_stack.png")

# Brand palette (from frontend/public/favicon.svg)
NAVY_TOP = (8, 24, 64)
NAVY_BOT = (0, 58, 138)
CARD_BG = (255, 255, 255)
BORDER = (226, 232, 240)
DOT_COLORS = [(248, 113, 113), (251, 191, 36), (52, 211, 153)]

W, H = 1600, 920


def gradient_bg(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        r = int(NAVY_TOP[0] + (NAVY_BOT[0] - NAVY_TOP[0]) * t)
        g = int(NAVY_TOP[1] + (NAVY_BOT[1] - NAVY_TOP[1]) * t)
        b = int(NAVY_TOP[2] + (NAVY_BOT[2] - NAVY_TOP[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def make_window(shot_path, target_w, bar_h=44, radius=18):
    """Return a browser-chrome framed screenshot at target_w width."""
    shot = Image.open(shot_path).convert("RGB")
    scale = target_w / shot.width
    new_h = int(shot.height * scale)
    shot = shot.resize((target_w, new_h), Image.LANCZOS)

    win_w = target_w + 24  # 12px side padding
    win_h = new_h + bar_h + 12
    win = Image.new("RGB", (win_w, win_h), CARD_BG)
    d = ImageDraw.Draw(win)

    # title bar
    d.rectangle([0, 0, win_w, bar_h], fill=(248, 250, 252))
    d.line([0, bar_h, win_w, bar_h], fill=BORDER, width=1)
    # traffic dots
    dx = 20
    for c in DOT_COLORS:
        d.ellipse([dx, bar_h // 2 - 5, dx + 10, bar_h // 2 + 5], fill=c)
        dx += 18
    # fake url pill
    pill_w, pill_h = 220, 22
    px = win_w // 2 - pill_w // 2
    py = bar_h // 2 - pill_h // 2
    d.rounded_rectangle([px, py, px + pill_w, py + pill_h], radius=pill_h // 2, fill=(241, 245, 249), outline=BORDER)

    # paste screenshot
    shot_x = 12
    shot_y = bar_h + 6
    mask = rounded_mask(shot.size, 10)
    win.paste(shot, (shot_x, shot_y), mask)
    d.rounded_rectangle([shot_x, shot_y, shot_x + target_w - 1, shot_y + new_h - 1], radius=10, outline=BORDER, width=1)

    # outer rounded frame + shadow handled by caller
    full = Image.new("RGBA", (win_w, win_h), (0, 0, 0, 0))
    full.paste(win, (0, 0))
    mask = rounded_mask((win_w, win_h), radius)
    full.putalpha(mask)
    return full


def rotate_with_shadow(layer, angle, shadow_blur=30, shadow_alpha=90, shadow_offset=(0, 18)):
    """Rotate layer, add drop shadow, return RGBA canvas-sized layer."""
    rot = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    sh = Image.new("RGBA", rot.size, (0, 0, 0, 0))
    alpha = rot.getchannel("A").point(lambda a: a * shadow_alpha // 255)
    sh.putalpha(alpha)
    sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))

    # composite shadow + layer
    out = Image.new("RGBA", rot.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (shadow_offset[0], shadow_offset[1]))
    out.alpha_composite(rot, (0, 0))
    return out


def font(size, bold=False):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(os.path.join("C:/Windows/Fonts", name), size)


def main():
    # back-to-front stack: (file, width, angle, x_anchor, top_y)
    layers_spec = [
        ("09_teknisi_queue.png", 290, -8, 0.15, 600),
        ("02_status_tracking.png", 350, 6, 0.83, 580),
        ("01_home_catalog.png", 480, -4, 0.50, 520),
        ("Dashboard.png", 780, 0, 0.50, 400),
    ]

    bg = gradient_bg(W, H).convert("RGBA")

    # subtle vignette dots? keep clean. Add soft radial light top-center.
    light = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(light)
    for i in range(6):
        r = 500 - i * 60
        alpha = 14 - i * 2
        ld.ellipse([W // 2 - r, 120 - r // 2, W // 2 + r, 120 + r // 2],
                   fill=(120, 170, 255, max(alpha, 0)))
    light = light.filter(ImageFilter.GaussianBlur(80))
    bg.alpha_composite(light)

    canvas = bg

    for fname, tw, angle, x_anchor, top_y in layers_spec:
        win = make_window(os.path.join(SHOTS, fname), tw)
        layer = rotate_with_shadow(win, angle)
        x = int(W * x_anchor - layer.width / 2)
        top = min(int(top_y), H - 10 - layer.height)
        canvas.alpha_composite(layer, (x, top))

    # caption
    d = ImageDraw.Draw(canvas)
    f_title = font(44, bold=True)
    f_sub = font(22)
    title = "Solea"
    sub = "Sistem Operasional Laundry Sepatu"
    tw = d.textlength(title, font=f_title)
    d.text((W / 2 - tw / 2, 80), title, font=f_title, fill=(255, 255, 255))
    tw2 = d.textlength(sub, font=f_sub)
    d.text((W / 2 - tw2 / 2, 140), sub, font=f_sub, fill=(190, 210, 245))

    # bottom fade to transparent-ish dark for text safety not needed; save RGB
    canvas.convert("RGB").save(OUT, "PNG", optimize=True)
    print("saved", OUT, canvas.size)


if __name__ == "__main__":
    main()
