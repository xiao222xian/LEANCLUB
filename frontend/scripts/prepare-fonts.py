"""Reproduce local static English outlines; runtime/build does not require Python.

Requires fontTools 4.25+ and the existing site font. Instances are renamed because
the upstream OFL lists Space Grotesk as a reserved font name.
"""
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

root = Path(__file__).resolve().parents[2]
source = root / "web/creator/city-vote/assets/fonts/sans.ttf"
destination = root / "frontend/public/fonts"
destination.mkdir(parents=True, exist_ok=True)

for weight in (300, 400, 500, 600, 700):
    font = TTFont(source, recalcTimestamp=False)
    instantiateVariableFont(font, {"wght": weight}, inplace=True)
    options = subset.Options()
    options.layout_features = ["kern"]
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
    options.name_legacy = True
    options.name_languages = ["*"]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    subsetter.subset(font)
    family = "Lean Design Grotesk"
    for record in font["name"].names:
        if record.nameID in (1, 4):
            value = family if record.nameID == 1 else f"{family} {weight}"
        elif record.nameID == 2:
            value = f"Weight {weight}"
        elif record.nameID in (3, 6):
            value = f"LeanDesignGrotesk-{weight}"
        else:
            continue
        record.string = value.encode(record.getEncoding())
    output = destination / f"lean-design-{weight}.ttf"
    font.save(output)
    print(f"{output.name}: {output.stat().st_size} bytes")
