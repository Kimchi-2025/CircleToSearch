import os
from pathlib import Path


GUIDE_PATH = Path(__file__).resolve().parents[1] / "data" / "musinsa_fashion_vision_prompt_v2.txt"


SYSTEM_PROMPT = """You identify one fashion product selected by a user's green outline in a photograph.
Consider products near the outline center, the fraction of each product inside it,
its visible area, and visual recognizability. Do not choose skin, face, hair, or background.
Select exactly one intended product; if none is identifiable, return status no_object
and null product, geometry, and keywords.

Identify category, specific product type, visible colors, pattern, text/logo,
apparent material, fit/shape, style, and a concise description. Use null for
unobservable properties. Do not invent a brand or hidden details. Preserve
specific visible text, logos, colors, prints, and fit in the product analysis.

For status success, search_keywords must be non-null with exactly these fields:
primary (one non-empty string), alternatives (exactly two non-empty strings),
and broad (one non-empty string). Generate all four in this SAME response.
Return distinct, short Korean shopping queries suitable for Musinsa search.
Prioritize product type, main color, and ONE useful visible design feature.
Translate a visible feature into a common shopping term only when accurate:
lettering, graphic, print, logo, or embroidery are not interchangeable.
Use at most ONE general design/fit/style term besides product type and color.
Keep fine details such as print layout or placement, slogans, place names,
isolated numbers, and exact English text in the product analysis, not the
default shopping queries. An unmistakable brand or model may be used only
if it improves shopping search. Do not force any particular design word.
Choose between lettering, graphic, print, logo, embroidery, etc. based on
what is actually visible. Keep each query to product type plus at most a color
and one design/fit/style term. Use different useful combinations so the four
queries can find different products, not four variations of one exact phrase.
primary = product type + main color + ONE common, representative design term,
omitting any feature that is not visible or useful for shopping search.
alternative 1 = product type + main color, dropping the design term first;
use product type + a common design term if color is unimportant or uncertain.
alternative 2 = product type + a different observed design or fit term;
omit color here only when it helps broaden the search.
broad = product type + a broader observed style/design term, without color
when wider coverage is useful. Never add fine layout modifiers to primary.
Apply the same rules to all fashion product types. Keep the product description
concise (about 60 Korean characters); retain distinctive visible details.

Estimate the visible outline of that SAME product only. Exclude skin, arms,
hands, other clothing, other objects, and background. Include visible parts of
the product outside the green outline when clearly the same object. Do not
imagine occluded parts. Return a bounding box and a simple polygon in the
provided image's pixel coordinates, with 0 <= x < width and 0 <= y < height.
Use about 8-24 vertices when reliable. This is an estimate, not a pixel mask.
If the outline cannot be estimated reliably, use null polygon and needs_review=true.
If no product is identified, use status no_object.
"""


def build_vision_instructions() -> str:
    if os.getenv("MUSINSA_VISION_CATEGORY_GUIDE", "false").strip().lower() != "true":
        return SYSTEM_PROMPT
    try:
        guide = GUIDE_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return SYSTEM_PROMPT
    if not guide:
        return SYSTEM_PROMPT
    return (SYSTEM_PROMPT + "\n\nOptional Musinsa fashion vocabulary reference. "
            "Identify the actual product type first. Use a listed term only if it fits; "
            "natural terms absent from this list are also allowed. Keep the four distinct "
            "Korean queries to roughly 2-4 key words each. Add only a useful main color "
            "or one salient feature; do not enumerate material, pattern, fit, and color "
            "together. Preserve the existing primary, two alternatives, and broad fields. "
            "Avoid exact logo text, numbers, and excessive visual detail.\n" + guide)
