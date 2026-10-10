#!/usr/bin/env python3
"""
Split a 17x11 base plan (300 dpi PNG from base_plan.py) into two portrait letter
pages at 100% scale, for printers without tabloid paper. The drawing (sheet margin
0.4 in) is 16.2 in wide; each page carries 8.2 in of it, overlapping 0.2 in at the
middle. Each page has a dashed line at the sheet's center: trim page 2 on its line
and lay it over page 1's line.

Content sits 0.15 in from the left and 0.3 in from the top of each page, inside the
~0.12 in hard margin of most inkjets.

  .venv\\Scripts\\python.exe scripts\\tile_letter.py output\\wf06_base_300.png -o output\\wf06_base_letter
"""
import argparse
from PIL import Image, ImageDraw, ImageFont

DPI = 300
PAGE = (int(8.5 * DPI), int(11 * DPI))
M = 0.4                    # base-plan sheet margin, in
OVER = 0.1                 # each page extends this far past the sheet center, in
X0, Y0 = 0.15, 0.3         # where the crop lands on the letter page, in


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="Base plan")
    a = ap.parse_args()

    sheet = Image.open(a.png).convert("RGB")
    W, H = sheet.size[0] / DPI, sheet.size[1] / DPI
    mid = W / 2
    spans = [(M, mid + OVER), (mid - OVER, W - M)]
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    pages = []
    for i, (x0, x1) in enumerate(spans):
        crop = sheet.crop((round(x0 * DPI), round(M * DPI), round(x1 * DPI), round((H - M) * DPI)))
        page = Image.new("RGB", PAGE, "white")
        page.paste(crop, (round(X0 * DPI), round(Y0 * DPI)))
        d = ImageDraw.Draw(page)
        cx = round((X0 + mid - x0) * DPI)                     # sheet center on this page
        for y in range(round(Y0 * DPI), round((Y0 + H - 2 * M) * DPI), 40):
            d.line([(cx, y), (cx, y + 20)], fill=(150, 150, 150), width=2)
        note = (f"{a.title} - page {i + 1} of 2 ({'left' if i == 0 else 'right'}). Print at 100% (actual size), letter. "
                + ("Overlap page 2 here: match the dashed lines." if i == 0 else "Trim on the dashed line and lay it over page 1's dashed line."))
        d.text((round(X0 * DPI), round((Y0 + H - 2 * M + 0.08) * DPI)), note, fill=(110, 110, 110), font=font)
        page.save(f"{a.out}_p{i + 1}.png", dpi=(DPI, DPI))
        pages.append(page)
    pages[0].save(f"{a.out}.pdf", save_all=True, append_images=pages[1:], resolution=DPI)
    print("wrote", f"{a.out}.pdf", f"{a.out}_p1.png", f"{a.out}_p2.png")


if __name__ == "__main__":
    main()
