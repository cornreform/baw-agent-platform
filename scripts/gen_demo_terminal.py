"""Generate clean demo terminal image for README — ASCII only."""
import os
from PIL import Image, ImageDraw

w, h = 780, 420
img = Image.new('RGB', (w, h), (10, 10, 20))
draw = ImageDraw.Draw(img)

# Terminal frame + window dots
draw.rectangle([20, 20, w-20, h-20], outline=(30, 30, 50), fill=(8, 8, 16))
draw.rectangle([20, 20, w-20, 48], fill=(16, 16, 30))
draw.ellipse([32, 30, 42, 40], fill=(255, 80, 80))
draw.ellipse([46, 30, 56, 40], fill=(255, 200, 60))
draw.ellipse([60, 30, 70, 40], fill=(60, 200, 80))

lines = [
    ('prompt', '$ baw --setup'),
    ('banner', '+----------------------+'),
    ('banner', '|   BAW Setup Wizard   |'),
    ('banner', '| Angel/Devil Court    |'),
    ('banner', '| Protocol-agnostic    |'),
    ('banner', '| Self-Evolution       |'),
    ('banner', '+----------------------+'),
    ('',       ''),
    ('prompt', '$ baw "analyze project structure"'),
    ('devil',  'DEVIL: I found 14 Python files.'),
    ('devil',  '  Score: 8/10  -- feasible but'),
    ('devil',  '  concerns about module deps'),
    ('',       ''),
    ('angel',  'ANGEL: Executing analysis...'),
    ('ok',     '  [OK] 4 modules read'),
    ('ok',     '  [OK] Found main entry'),
    ('ok',     '  [OK] 6 tools registered'),
    ('',       ''),
    ('cost',   'Calls: 1  |  Total tokens: 1,427'),
    ('',       ''),
    ('prompt', '$ baw --board  # Live dashboard'),
]

colors = {
    'prompt': (68, 180, 120),
    'banner': (100, 130, 210),
    'devil':  (200, 140, 140),
    'angel':  (140, 200, 220),
    'ok':     (100, 200, 140),
    'cost':   (160, 160, 180),
    '':       (180, 180, 200),
}

y = 60
for typ, text in lines:
    c = colors.get(typ, (180, 180, 200))
    draw.text((28, y), text, fill=c)
    y += 21

img.save('docs/demo-terminal.png')
print(f"Saved: {os.path.getsize('docs/demo-terminal.png')} bytes")
