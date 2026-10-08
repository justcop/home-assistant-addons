"""Render the app's existing pound-mark identity as Home Assistant PNG assets.
Development only: requires Pillow and DejaVu Sans; no runtime dependency.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SCALE = 4

def font(size):
    return ImageFont.truetype(FONT, size * SCALE)

def emblem(image, x, y, size):
    d = ImageDraw.Draw(image)
    box = (x*SCALE, y*SCALE, (x+size)*SCALE, (y+size)*SCALE)
    d.rounded_rectangle(box, radius=size*SCALE//4, fill='#163e36')
    d.text(((x+size/2)*SCALE, (y+size/2-3)*SCALE), '£', font=font(round(size*.65)), fill='#c4e7b7', anchor='mm')
    d.ellipse(((x+size*.74)*SCALE,(y+size*.73)*SCALE,(x+size*.86)*SCALE,(y+size*.85)*SCALE),fill='#dfb96b')

icon = Image.new('RGBA', (128*SCALE,128*SCALE))
emblem(icon,0,0,128)
icon = icon.resize((128,128),Image.Resampling.LANCZOS)
icon.save(ROOT/'icon.png')
icon.save(ROOT/'app/static/icon.png')
logo = Image.new('RGBA',(250*SCALE,100*SCALE))
ImageDraw.Draw(logo).rounded_rectangle((0, 0, 250*SCALE-1, 100*SCALE-1), radius=16*SCALE, fill='#f2f5ee')
emblem(logo,4,14,72)
d=ImageDraw.Draw(logo)
d.text((90*SCALE,27*SCALE),'Money',font=font(21),fill='#163e36')
d.text((90*SCALE,54*SCALE),'Locations',font=font(21),fill='#163e36')
logo.resize((250,100),Image.Resampling.LANCZOS).save(ROOT/'logo.png')
