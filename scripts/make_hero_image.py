"""Generate a stacked-screenshots hero image for the Solea README."""
from PIL import Image, ImageDraw, ImageFilter
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(BASE, "frontend", "public", "screenshots")
OUT = os.path.join(SHOTS, "hero_stack.png")

CARD_BG = (255, 255, 255)
BORDER = (226, 232, 240)
DOT_COLORS = [(248, 113, 113), (251, 191, 36), (52, 211, 153)]

W, H = 1500, 780


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


def main():
    # back-to-front stack: (file, width, angle, x_anchor, top_y)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    layers_spec = [
        ("09_teknisi_queue.png", 280, -7, 0.16, 430),
        ("02_status_tracking.png", 340, 5, 0.82, 410),
        ("01_home_catalog.png", 460, -3, 0.50, 450),
        ("Dashboard.png", 800, 0, 0.50, 240),
    ]

    for fname, tw, angle, x_anchor, top_y in layers_spec:
        win = make_window(os.path.join(SHOTS, fname), tw)
        layer = rotate_with_shadow(win, angle)
        x = int(W * x_anchor - layer.width / 2)
        top = min(int(top_y), H - 10 - layer.height)
        canvas.alpha_composite(layer, (x, top))

    canvas.save(OUT, "PNG", optimize=True)
    print("saved", OUT, canvas.size)


if __name__ == "__main__":
    main()
