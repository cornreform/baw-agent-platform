"""Generate demo terminal image for README."""
import os
from PIL import Image, ImageDraw

w, h = 800, 500
img = Image.new('RGB', (w, h), (10, 10, 20))
draw = ImageDraw.Draw(img)

# Draw terminal frame
draw.rectangle([20, 20, w-20, h-20], outline=(30, 30, 50), fill=(8, 8, 16))
draw.rectangle([20, 20, w-20, 50], fill=(16, 16, 30))
draw.ellipse([32, 30, 42, 40], fill=(255, 80, 80))
draw.ellipse([46, 30, 56, 40], fill=(255, 200, 60))
draw.ellipse([60, 30, 70, 40], fill=(60, 200, 80))

lines = [
    ('prompt', '$ baw --setup'),
    ('out',    '\u2554\u2550\u2550 BAW Setup Wizard \u2550\u2550\u2550\u2550\u2557'),
    ('out',    '\u2551  \ud83e\ude84 Angel/Devil Court  \u2551'),
    ('out',    '\u2551  \ud83d\udd0c Protocol-agnostic  \u2551'),
    ('out',    '\u2551  \ud83e\udde0 Self-Evolution     \u2551'),
    ('out',    '\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d'),
    ('prompt', ''),
    ('prompt', '$ baw "analyze project structure"'),
    ('out',    '\u26ab Devil: I see 14 Python files.'),
    ('out',    '   Score: 8/10 - feasible but'),
    ('out',    '   concerns about module deps'),
    ('out',    ''),
    ('out',    '\ud83e\ude84 Angel: Executing analysis...'),
    ('out',    '   \u2713 4 modules read'),
    ('out',    '   \u2713 Found main entry in baw'),
    ('out',    '   \u2713 6 tools registered'),
    ('out',    '\ud83d\udcca Token: 1 call - 1,427 tokens'),
    ('prompt', ''),
    ('prompt', '$ baw --board  # Live dashboard'),
]

colors = {
    'prompt': (68, 180, 120),
    'out': (180, 180, 200),
}

y = 65
for typ, text in lines:
    c = colors.get(typ, (180, 180, 200))
    draw.text((30, y), text, fill=c)
    y += 20

img.save('docs/demo-terminal.png')
print('Saved docs/demo-terminal.png', os.path.getsize('docs/demo-terminal.png'))
