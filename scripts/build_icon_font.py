"""يبني ملف Material Symbols Outlined المستضاف ذاتيًا في app/static/fonts/material-symbols/.

الخط الرسمي (Variable: FILL, GRAD, opsz, wght) حجمه ~4MB — تحميله على شبكة
موبايل يتخطى فترة `font-display: block` (~3 ثواني) فتظهر أسماء الأيقونات كنص
("shopping_bag", "local_shipping"...) عند الـ Reload. القوالب لا تغيّر إلا محور
FILL (0/1)، فنثبّت باقي المحاور على القيم المستخدمة (wght=400, GRAD=0, opsz=24)
→ ~450KB مع الإبقاء على كل الأيقونات (لا subsetting بالأسماء، لأن بعض الأسماء
ديناميكية من Python/JS).

لو احتجت محورًا إضافيًا مستقبلًا (مثل wght متغير) أضفه هنا وأعد البناء.

التشغيل (يتطلب fonttools + brotli):
    python scripts/build_icon_font.py
"""
from __future__ import annotations

import tempfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

SOURCE_URL = (
    "https://raw.githubusercontent.com/google/material-design-icons/master/"
    "variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.woff2"
)
PINNED_AXES = {"wght": 400, "GRAD": 0, "opsz": 24}
OUT = (
    Path(__file__).resolve().parent.parent
    / "app" / "static" / "fonts" / "material-symbols" / "MaterialSymbolsOutlined.woff2"
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "source.woff2"
        urllib.request.urlretrieve(SOURCE_URL, src)
        font = TTFont(src)
        pinned = instancer.instantiateVariableFont(font, PINNED_AXES)
        pinned.flavor = "woff2"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        pinned.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
