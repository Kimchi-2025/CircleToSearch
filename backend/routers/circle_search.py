import io
import json
import math
import warnings

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from services.vision_service import VisionError, analyze
from services.recommendation_service import RecommendationError, recommend
from services.tryon_service import TryOnError, generate_tryon

router = APIRouter(prefix="/api/circle-search", tags=["circle-search"])
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 8000
MAX_POLYGON_POINTS = 2048
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def fail(message: str) -> None:
    raise HTTPException(status_code=422, detail=message)


def parse_coordinates(raw: str, name: str, length: int | None = None) -> list:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        fail(f"{name}: 올바른 JSON 배열이 아닙니다.")
    if not isinstance(value, list) or (length is not None and len(value) != length):
        fail(f"{name}: 배열 길이가 올바르지 않습니다.")
    return value


def check_pair(value: list, name: str, width: int, height: int) -> tuple[int, int]:
    if len(value) != 2 or any(type(item) is not int for item in value):
        fail(f"{name}: 정수 좌표 [x, y]가 필요합니다.")
    x, y = value
    if not (0 <= x < width and 0 <= y < height):
        fail(f"{name}: 이미지 범위를 벗어났습니다.")
    return x, y


def validate_selection(
    width: int, height: int, point_raw: str, box_raw: str, polygon_raw: str
) -> tuple[list[int], list[int], list[list[int]]]:
    point = list(check_pair(parse_coordinates(point_raw, "point", 2), "point", width, height))
    box = parse_coordinates(box_raw, "box", 4)
    if any(type(item) is not int for item in box):
        fail("box: 정수 좌표 [left, top, right, bottom]이 필요합니다.")
    left, top, right, bottom = box
    if not (0 <= left < right < width and 0 <= top < bottom < height):
        fail("box: 이미지 범위 내에서 왼쪽/위쪽이 오른쪽/아래쪽보다 작아야 합니다.")
    if not (left <= point[0] <= right and top <= point[1] <= bottom):
        fail("point: box 범위 안에 있어야 합니다.")

    polygon = parse_coordinates(polygon_raw, "polygon")
    if not (3 <= len(polygon) <= MAX_POLYGON_POINTS):
        fail("polygon: 3개 이상 2048개 이하의 점이 필요합니다.")
    checked = [list(check_pair(pair, "polygon", width, height)) if isinstance(pair, list) else fail("polygon: 좌표 배열이 필요합니다.") for pair in polygon]
    xs, ys = [pair[0] for pair in checked], [pair[1] for pair in checked]
    if [min(xs), min(ys), max(xs), max(ys)] != box:
        fail("box: polygon의 경계와 일치해야 합니다.")
    area = abs(sum(
        checked[i][0] * checked[(i + 1) % len(checked)][1]
        - checked[(i + 1) % len(checked)][0] * checked[i][1]
        for i in range(len(checked))
    )) / 2
    if right - left < 8 or bottom - top < 8 or area < 64:
        fail("polygon: 선택 영역이 너무 작습니다.")
    return point, box, checked


async def read_image(image: UploadFile) -> tuple[bytes, tuple[int, int]]:
    if image.content_type not in ALLOWED_FORMATS.values():
        raise HTTPException(status_code=415, detail="JPEG, PNG, WebP 이미지만 허용합니다.")
    chunks = []
    size = 0
    while chunk := await image.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="이미지 크기는 10 MB 이하여야 합니다.")
        chunks.append(chunk)
    if not chunks:
        fail("이미지 파일이 비어 있습니다.")
    data = b"".join(chunks)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as opened:
                fmt = opened.format
                actual_size = opened.size
                opened.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        fail("이미지 파일을 읽거나 검증할 수 없습니다.")
    if ALLOWED_FORMATS.get(fmt) != image.content_type:
        fail("파일 내용과 MIME 형식이 일치하지 않습니다.")
    if not (0 < actual_size[0] <= MAX_IMAGE_SIDE and 0 < actual_size[1] <= MAX_IMAGE_SIDE):
        fail("이미지의 가로와 세로는 각각 8000 px 이하여야 합니다.")
    return data, actual_size


@router.post("/segment")
async def segment(
    image: UploadFile = File(...),
    width: int = Form(...),
    height: int = Form(...),
    point: str = Form(...),
    box: str = Form(...),
    polygon: str = Form(...),
) -> dict:
    if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE):
        fail("width와 height는 1~8000 px 범위여야 합니다.")
    image_bytes, actual_size = await read_image(image)
    if actual_size != (width, height):
        fail("전달한 이미지 크기와 실제 이미지 크기가 다릅니다.")
    checked_point, checked_box, checked_polygon = validate_selection(
        width, height, point, box, polygon
    )
    try:
        analysis = await run_in_threadpool(
            analyze, image_bytes, width, height, checked_point, checked_polygon
        )
    except VisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        **analysis,
        "sam_enabled": False,
        "message": ("선택한 패션 상품을 분석했습니다." if analysis["status"] == "success"
                    else "상품은 분석했지만 검색어를 만들 수 없어 유사 상품 검색은 사용할 수 없습니다."
                    if analysis["status"] == "search_unavailable"
                    else "선택 영역에서 식별 가능한 패션 상품을 찾지 못했습니다."),
        "selection": {
            "point": checked_point,
            "box": checked_box,
            "polygon_points": len(checked_polygon),
        },
    }


@router.post('/recommend')
async def recommend_products(crop: UploadFile = File(...), analysis: str = Form(...)) -> dict:
    try:
        payload = json.loads(analysis)
    except (json.JSONDecodeError, TypeError):
        fail('analysis: 올바른 JSON 객체가 아닙니다.')
    if not isinstance(payload, dict):
        fail('analysis: 올바른 JSON 객체가 아닙니다.')
    selected = payload.get('selected_object')
    words = payload.get('search_keywords')
    if not isinstance(selected, dict) or not isinstance(words, dict):
        fail('analysis: 상품 정보와 검색어가 필요합니다.')
    description = selected.get('description')
    if not isinstance(description, str) or not (1 <= len(description.strip()) <= 1000):
        fail('analysis: 상품 설명이 필요합니다.')
    primary, alternatives, broad = words.get('primary'), words.get('alternatives'), words.get('broad')
    keywords = [primary, *(alternatives if isinstance(alternatives, list) else []), broad]
    if (len(keywords) != 4 or any(not isinstance(word, str) or
                                  not (1 <= len(word.strip()) <= 100) for word in keywords)):
        fail('analysis: 핵심·대체 2개·광범위 검색어가 필요합니다.')
    crop_bytes, _ = await read_image(crop)
    try:
        return await run_in_threadpool(recommend, crop_bytes, selected, keywords)
    except RecommendationError as exc:
        raise HTTPException(status_code=exc.http_status,
                            detail={'search_status': exc.status, 'message': str(exc),
                                    'search_diagnostics': exc.diagnostics}) from exc


@router.post('/try-on')
async def try_on(image: UploadFile = File(...), product: str = Form(...),
                 analysis: str = Form(...)) -> dict:
    try:
        item, context = json.loads(product), json.loads(analysis)
    except (json.JSONDecodeError, TypeError):
        fail('상품 및 분석 정보는 올바른 JSON 객체여야 합니다.')
    if not isinstance(item, dict) or not isinstance(context, dict):
        fail('상품 및 분석 정보는 올바른 JSON 객체여야 합니다.')
    product_id, name, image_url = (item.get('product_id'), item.get('product_name'),
                                   item.get('image_url'))
    if (not isinstance(product_id, str) or not product_id.isdecimal() or len(product_id) > 20 or
            not isinstance(name, str) or not (1 <= len(name.strip()) <= 200) or
            not isinstance(image_url, str) or not (1 <= len(image_url) <= 2048)):
        fail('유효한 무신사 상품 ID, 상품명, 이미지 주소가 필요합니다.')
    brand = item.get('brand')
    if brand is not None and (not isinstance(brand, str) or len(brand) > 100):
        fail('브랜드 정보가 올바르지 않습니다.')
    product_category = item.get('category')
    if product_category is not None and (not isinstance(product_category, str) or len(product_category) > 100):
        fail('추천 상품 카테고리가 올바르지 않습니다.')
    selected, box = context.get('selected_object'), context.get('bounding_box')
    if not isinstance(selected, dict) or not isinstance(selected.get('product_type'), str):
        fail('기존 상품 분석의 종류 정보가 필요합니다.')
    product_type = selected['product_type'].strip()
    description = selected.get('description')
    category = selected.get('category')
    if (not (1 <= len(product_type) <= 100) or
            (description is not None and (not isinstance(description, str) or len(description) > 1000)) or
            (category is not None and (not isinstance(category, str) or len(category) > 100))):
        fail('기존 상품 분석 정보가 올바르지 않습니다.')
    person_bytes, (width, height) = await read_image(image)
    if box is not None:
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in box)):
            fail('선택 영역 Bounding Box가 올바르지 않습니다.')
        left, top, right, bottom = box
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            fail('선택 영역이 사진 범위를 벗어났습니다.')
    try:
        return await run_in_threadpool(
            generate_tryon, person_bytes,
            {'product_id': product_id, 'product_name': name.strip(), 'brand': brand,
             'image_url': image_url, 'category': product_category},
            {'product_type': product_type, 'category': category,
             'description': description}, box, width, height,
        )
    except TryOnError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
