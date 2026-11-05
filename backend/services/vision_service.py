import base64
import hashlib
import io
import json
import logging
import math
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import Lock
from typing import Literal

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from PIL import Image, ImageDraw
from pydantic import BaseModel

from services.vision_prompt import build_vision_instructions

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
logger = logging.getLogger('uvicorn.error')
DEFAULT_MODEL = "gpt-6-luna"
MAX_VISION_SIDE = 1536
CACHE_SIZE = 32
_cache: OrderedDict[str, dict] = OrderedDict()
_pending: dict[str, Future] = {}
_lock = Lock()


class SelectedObject(BaseModel):
    category: str | None
    product_type: str | None
    description: str | None
    dominant_color: str | None
    secondary_colors: list[str]
    pattern: str | None
    visible_text_or_logo: str | None
    material_appearance: str | None
    fit_or_shape: str | None
    style: str | None


class Geometry(BaseModel):
    bounding_box: list[float] | None
    polygon: list[list[float]] | None
    needs_review: bool


class SearchKeywords(BaseModel):
    primary: str
    alternatives: list[str]
    broad: str


class VisionOutput(BaseModel):
    status: Literal["success", "no_object"]
    selected_object: SelectedObject | None
    geometry: Geometry | None
    search_keywords: SearchKeywords | None


class VisionError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _shopping_term(value, aliases=(), allow_raw=True):
    if not isinstance(value, str) or not value.strip():
        return ''
    normalized = value.strip().lower()
    for source, target in aliases:
        if source in normalized:
            return target
    cleaned = ' '.join(value.split())
    return cleaned if allow_raw and len(cleaned) <= 24 and re.fullmatch(r'[가-힣 ]+', cleaned) else ''


def _recover_keywords(selected: SelectedObject, supplied: SearchKeywords | None) -> dict | None:
    existing = ([supplied.primary,
                 supplied.alternatives[0] if len(supplied.alternatives) > 0 else '',
                 supplied.alternatives[1] if len(supplied.alternatives) > 1 else '',
                 supplied.broad] if supplied is not None else [])
    if len(existing) == 4 and all(isinstance(word, str) and word.strip() for word in existing):
        stripped = [word.strip() for word in existing]
        if len(set(stripped)) == 4:
            return {'primary': stripped[0], 'alternatives': stripped[1:3], 'broad': stripped[3]}

    type_aliases = (('t-shirt', '티셔츠'), ('sneaker', '스니커즈'), ('shirt', '셔츠'),
                    ('pants', '바지'), ('trouser', '바지'), ('jeans', '청바지'),
                    ('shoes', '신발'), ('shoe', '신발'), ('bag', '가방'),
                    ('watch', '시계'), ('belt', '벨트'), ('glasses', '안경'))
    product_type = (_shopping_term(selected.product_type, type_aliases)
                    or _shopping_term(selected.category, type_aliases))
    if product_type in {'상의', '하의', '의류', '옷', '패션', '패션용품', '액세서리', '악세서리'}:
        product_type = ''
    if not product_type:
        return None

    colors = (('네이비', '네이비'), ('navy', '네이비'), ('검정', '검정'), ('블랙', '검정'),
              ('black', '검정'), ('화이트', '흰색'), ('white', '흰색'), ('흰', '흰색'),
              ('그레이', '회색'), ('gray', '회색'), ('베이지', '베이지'), ('카키', '카키'))
    designs = (('레터링', '레터링'), ('lettering', '레터링'), ('그래픽', '그래픽'),
               ('graphic', '그래픽'), ('스트라이프', '스트라이프'), ('stripe', '스트라이프'),
               ('체크', '체크'), ('로고', '로고'), ('프린팅', '프린팅'), ('프린트', '프린트'),
               ('자수', '자수'), ('embroidery', '자수'), ('무지', '무지'))
    fits = (('오버핏', '오버핏'), ('oversize', '오버핏'), ('루즈핏', '루즈핏'),
            ('슬림핏', '슬림핏'), ('와이드', '와이드'), ('크롭', '크롭'))
    styles = (('캐주얼', '캐주얼'), ('casual', '캐주얼'), ('스포티', '스포티'),
              ('sporty', '스포티'), ('빈티지', '빈티지'), ('vintage', '빈티지'),
              ('미니멀', '미니멀'), ('minimal', '미니멀'))
    color = _shopping_term(selected.dominant_color, colors)
    design = (_shopping_term(selected.pattern, designs, allow_raw=False)
              or _shopping_term(selected.description, designs, allow_raw=False))
    visible_text = (selected.visible_text_or_logo or '').strip().lower()
    if not design and visible_text and not any(
        absent in visible_text for absent in ('없음', '없다', '보이지', '확인 불가', 'none', 'no logo')
    ):
        # Visible text alone does not establish whether the item is lettering,
        # a graphic, or a print; only keep an explicitly identified logo.
        design = '로고' if '로고' in visible_text or 'logo' in visible_text else ''
    fit = (_shopping_term(selected.fit_or_shape, fits, allow_raw=False)
           or _shopping_term(selected.description, fits, allow_raw=False))
    style = (_shopping_term(selected.style, styles, allow_raw=False)
             or _shopping_term(selected.description, styles, allow_raw=False))
    candidates = [
        ' '.join(part for part in (color, design, product_type) if part),
        ' '.join(part for part in (design, product_type) if part),
        ' '.join(part for part in (fit, product_type) if part),
        ' '.join(part for part in (style, product_type) if part),
        ' '.join(part for part in (color, product_type) if part),
        product_type,
    ]
    recovered, used = [], set()
    for index in range(4):
        supplied_word = existing[index].strip() if index < len(existing) and isinstance(existing[index], str) else ''
        word = supplied_word if supplied_word and supplied_word not in used else next(
            (candidate for candidate in candidates if candidate and candidate not in used), '')
        if not word:
            return None
        recovered.append(word)
        used.add(word)
    return {'primary': recovered[0], 'alternatives': recovered[1:3], 'broad': recovered[3]}


def _segments_intersect(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    ab_c, ab_d = cross(a, b, c), cross(a, b, d)
    cd_a, cd_b = cross(c, d, a), cross(c, d, b)
    if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0])
            or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
        return False
    return ab_c * ab_d <= 0 and cd_a * cd_b <= 0


def _valid_polygon(points: list[list[int]], width: int, height: int) -> bool:
    if not 3 <= len(points) <= 128 or len({tuple(point) for point in points}) < 3:
        return False
    if any(points[i] == points[(i + 1) % len(points)] for i in range(len(points))):
        return False
    if any(not (0 <= x < width and 0 <= y < height) for x, y in points):
        return False
    area = abs(sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )) / 2
    if area < 64:
        return False
    count = len(points)
    return not any(
        _segments_intersect(points[i], points[(i + 1) % count], points[j], points[(j + 1) % count])
        for i in range(count) for j in range(i + 2, count)
        if not (i == 0 and j == count - 1)
    )


def normalize_output(output: VisionOutput, width: int, height: int, shown_size: tuple[int, int]) -> dict:
    if output.status == "no_object":
        return {"status": "no_object", "selected_object": None,
                "geometry": None, "search_keywords": None}
    if output.selected_object is None:
        raise VisionError("OpenAI 분석 결과에 필수 상품 정보가 없습니다.")

    geometry = {"bounding_box": None, "polygon": None,
                "geometry_type": "estimated", "needs_review": True}
    if output.geometry is not None:
        source_w, source_h = shown_size

        def convert(pair):
            if not isinstance(pair, list) or len(pair) != 2 or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) for value in pair
            ):
                return None
            x, y = pair
            if not (0 <= x < source_w and 0 <= y < source_h):
                return None
            return [min(width - 1, round(x * width / source_w)),
                    min(height - 1, round(y * height / source_h))]

        box = output.geometry.bounding_box
        if box is not None and len(box) == 4:
            corners = [convert(box[:2]), convert(box[2:])]
            if all(corners):
                left, top = corners[0]
                right, bottom = corners[1]
                if 0 <= left < right < width and 0 <= top < bottom < height:
                    geometry["bounding_box"] = [left, top, right, bottom]
        polygon = output.geometry.polygon
        if polygon is not None and 3 <= len(polygon) <= 128:
            converted = [convert(pair) for pair in polygon]
            if len(converted) > 3 and converted[0] is not None and converted[0] == converted[-1]:
                converted.pop()
            if all(pair is not None for pair in converted) and _valid_polygon(converted, width, height):
                polygon_box = [min(p[0] for p in converted), min(p[1] for p in converted),
                               max(p[0] for p in converted), max(p[1] for p in converted)]
                model_box = geometry["bounding_box"]
                tolerance = max(8, round(0.05 * max(width, height)))
                if model_box is None or all(abs(a - b) <= tolerance for a, b in zip(model_box, polygon_box)):
                    geometry["polygon"] = converted
                    geometry["bounding_box"] = polygon_box
        geometry["needs_review"] = output.geometry.needs_review or geometry["polygon"] is None

    keywords = _recover_keywords(output.selected_object, output.search_keywords)
    if keywords is None:
        logger.warning('Vision search keywords unavailable after deterministic recovery')
    elif output.search_keywords is None or keywords != output.search_keywords.model_dump():
        logger.info('Vision search keywords recovered from selected product attributes')
    return {"status": "success" if keywords else "search_unavailable",
            "selected_object": output.selected_object.model_dump(),
            "geometry": geometry,
            "search_keywords": keywords}


def _marked_image(image_bytes: bytes, polygon: list[list[int]], point: list[int]) -> tuple[str, tuple[int, int]]:
    with Image.open(io.BytesIO(image_bytes)) as source:
        original = source.convert("RGB")
    scale = min(1, MAX_VISION_SIDE / max(original.size))
    size = (max(1, round(original.width * scale)), max(1, round(original.height * scale)))
    scale_x, scale_y = size[0] / original.width, size[1] / original.height
    if size != original.size:
        original = original.resize(size, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(original)
    line = [(round(x * scale_x), round(y * scale_y)) for x, y in polygon]
    draw.line(line + [line[0]], fill="#8aff00", width=max(3, round(4 * scale)))
    cx, cy = round(point[0] * scale_x), round(point[1] * scale_y)
    radius = max(4, round(6 * scale))
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill="#8aff00", outline="#111111", width=2)
    output = io.BytesIO()
    original.save(output, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii"), size


def _call_vision(image_bytes: bytes, width: int, height: int,
                 point: list[int], polygon: list[list[int]], model: str,
                 instructions: str) -> dict:
    started = time.monotonic()
    try:
        image_url, shown_size = _marked_image(image_bytes, polygon, point)
    except Exception as exc:
        raise VisionError("Vision 입력 이미지를 준비하지 못했습니다.") from exc
    preprocess_ms = round((time.monotonic() - started) * 1000)
    logger.info('Vision preprocess_ms=%s image_width=%s image_height=%s',
                preprocess_ms, shown_size[0], shown_size[1])
    prompt = (f"The image is {shown_size[0]} x {shown_size[1]} pixels. "
              "Use these pixel coordinates, not normalized coordinates. "
              "The green line is the approximate selection; the green dot is its center.")
    api_started = time.monotonic()
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=45.0, max_retries=0)
        response = client.responses.parse(
            model=model,
            **({'reasoning': {'effort': 'low'}} if model == DEFAULT_MODEL else {}),
            instructions=instructions,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_url, "detail": "high"},
            ]}],
            text_format=VisionOutput,
            store=False,
            max_output_tokens=1800,
        )
    except APITimeoutError as exc:
        raise VisionError("OpenAI 분석 시간이 초과되었습니다. 잠시 후 다시 시도하세요.", 504) from exc
    except APIConnectionError as exc:
        raise VisionError("OpenAI 서비스에 연결하지 못했습니다.", 502) from exc
    except APIStatusError as exc:
        if exc.status_code == 429:
            raise VisionError("OpenAI 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.", 429) from exc
        logger.warning("OpenAI status=%s model=%s", exc.status_code, model)
        raise VisionError("OpenAI 분석 요청이 실패했습니다. 모델 설정과 API 계정을 확인하세요.", 502) from exc
    except Exception as exc:
        logger.error("Vision processing failed (model=%s, error_type=%s)", model, type(exc).__name__)
        raise VisionError("Vision 분석 결과를 처리하지 못했습니다.") from exc
    finally:
        logger.info('Vision api_and_sdk_parse_ms=%s', round((time.monotonic() - api_started) * 1000))

    validation_started = time.monotonic()
    if response.output_parsed is None:
        raise VisionError("OpenAI가 분석 결과를 반환하지 않았습니다.")
    try:
        result = normalize_output(response.output_parsed, width, height, shown_size)
    finally:
        logger.info('Vision validation_ms=%s', round((time.monotonic() - validation_started) * 1000))
    usage = response.usage
    duration_ms = round((time.monotonic() - started) * 1000)
    metadata = {"model": response.model or model, "duration_ms": duration_ms,
                "usage": {"input_tokens": usage.input_tokens if usage else None,
                          "output_tokens": usage.output_tokens if usage else None,
                          "total_tokens": usage.total_tokens if usage else None}}
    logger.info("Vision model=%s total_analysis_ms=%s input_tokens=%s output_tokens=%s",
                metadata["model"], duration_ms, metadata["usage"]["input_tokens"],
                metadata["usage"]["output_tokens"])
    return {**result, "metadata": metadata}


def analyze(image_bytes: bytes, width: int, height: int,
            point: list[int], polygon: list[list[int]]) -> dict:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise VisionError("OpenAI API 키가 없습니다. backend/.env에 OPENAI_API_KEY를 설정하세요.", 503)
    model = os.getenv("OPENAI_VISION_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    instructions = build_vision_instructions()
    digest = hashlib.sha256(image_bytes + json.dumps(
        {"point": point, "polygon": polygon, "model": model,
         "instructions_hash": hashlib.sha256(instructions.encode("utf-8")).hexdigest()},
        separators=(",", ":")
    ).encode()).hexdigest()
    with _lock:
        cached = _cache.get(digest)
        if cached is not None:
            _cache.move_to_end(digest)
            return {**cached, "metadata": {**cached["metadata"], "cached": True}}
        pending = _pending.get(digest)
        if pending is None:
            pending = Future()
            _pending[digest] = pending
            owner = True
        else:
            owner = False
    if not owner:
        try:
            result = pending.result(timeout=60)
        except FutureTimeoutError as exc:
            raise VisionError("진행 중인 분석 시간이 초과되었습니다.", 504) from exc
        return {**result, "metadata": {**result["metadata"], "cached": True}}
    try:
        result = _call_vision(image_bytes, width, height, point, polygon, model, instructions)
        with _lock:
            _cache[digest] = result
            _cache.move_to_end(digest)
            if len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
        pending.set_result(result)
        return {**result, "metadata": {**result["metadata"], "cached": False}}
    except Exception as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _lock:
            _pending.pop(digest, None)
