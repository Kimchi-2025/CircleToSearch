"""One-shot upper-body image edit using the existing person and catalog photos."""

import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
import time
from concurrent.futures import Future
from threading import Lock
from urllib.parse import urlparse

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from PIL import Image, ImageOps, UnidentifiedImageError

from services.musinsa_service import product_family
from services.recommendation_service import _safe_image_host, cache_catalog_image, cached_catalog_image
from services.tryon_prompt import build_tryon_prompt

logger = logging.getLogger('uvicorn.error')
DEFAULT_MODEL = 'gpt-image-2.5-flare'
MODELS = {'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'}
QUALITIES = {'low', 'medium', 'high', 'auto'}
SIZES = {'auto', '1024x1024', '1024x1536', '1536x1024'}
TOP_FAMILIES = {'tshirt', 'shirt', 'sweatshirt', 'hoodie', 'knit', 'outer'}
LOWER_FAMILIES = {'pants', 'shorts', 'skirt'}
ACCESSORY_REGIONS = {'bag', 'hat', 'glasses', 'watch', 'belt', 'accessory'}
MAX_PRODUCT_BYTES = 5 * 1024 * 1024
MAX_RESULT_BYTES = 12 * 1024 * 1024
_pending: dict[str, Future] = {}
_lock = Lock()


class TryOnError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def garment_region(product_type: str) -> str | None:
    family = product_family(product_type)
    if family in TOP_FAMILIES:
        return 'upper'
    if family in LOWER_FAMILIES:
        return 'lower'
    if family == 'shoes':
        return 'shoes'
    if family in {'bag', 'glasses'}:
        return family
    if family == 'watch':
        return 'watch'
    if family == 'belt':
        return 'belt'
    name = product_type.lower()
    if any(term in name for term in ('상의', '아우터', '자켓', '재킷', '점퍼', '코트', '패딩',
                                     '블레이저', '바람막이', 'outerwear', 'jacket', 'coat',
                                     'puffer', 'blazer')):
        return 'upper'
    if any(term in name for term in ('하의', '트라우저', '쇼츠', '슬랙스', '데님', '조거',
                                     'pants', 'trouser', 'jeans', 'shorts', 'skirt',
                                     'denim', 'slacks')):
        return 'lower'
    if any(term in name for term in ('모자', '볼캡', '비니', '버킷햇', '버킷 햇', '페도라')):
        return 'hat'
    if any(term in name for term in ('가방', '백팩', '숄더', '크로스', '토트', '핸드백', '파우치')):
        return 'bag'
    if re.search(r'\b(?:backpacks?|handbags?|crossbody(?:\s+bags?)?|shoulder\s+bags?|tote(?:\s+bags?)?|bags?)\b', name):
        return 'bag'
    if re.search(r'\b(?:sunglasses|eyeglasses|glasses|eyewear|spectacles)\b', name):
        return 'glasses'
    if re.search(r'\b(?:hats?|caps?|beanies?|bucket\s+hats?|fedoras?)\b', name):
        return 'hat'
    if re.search(r'\b(?:wrist\s*)?watch(?:es)?\b', name):
        return 'watch'
    if any(term in name for term in ('허리띠', '벨트')) or re.search(r'\bbelts?\b', name):
        return 'belt'
    if re.search(r'\b(?:sneakers?|shoes?|boots?|sandals?|loafers?|heels?|pumps?|slippers?)\b', name):
        return 'shoes'
    if (any(term in name for term in ('t-shirt', 'tshirt', 'shirt', 'blouse', 'hoodie',
                                     'knit', 'cardigan')) or re.search(r'\btee\b', name)):
        return 'upper'
    if (any(term in name for term in ('반지', '목걸이', '귀걸이', '팔찌', '브로치', '스카프',
                                     '넥타이', '장갑', '헤어핀', '헤어밴드', '발찌', '커프스')) or
            re.search(r'\b(?:rings?|necklaces?|earrings?|bracelets?|brooches?|scarves?|'
                      r'neckties?|gloves?|hairpins?|headbands?|anklets?|cufflinks?)\b', name)):
        return 'accessory'
    if (any(term in name for term in ('액세서리', '악세사리', '패션잡화', '주얼리')) or
            re.search(r'\baccessor(?:y|ies)\b', name)):
        return 'accessory_group'
    return None


def _selected_region(selected: dict) -> str | None:
    type_region = garment_region(selected.get('product_type') or '')
    category_region = garment_region(selected.get('category') or '')
    if type_region == 'accessory_group' and category_region in ACCESSORY_REGIONS:
        return category_region
    if category_region == 'accessory_group' and type_region in ACCESSORY_REGIONS:
        return type_region
    if type_region and category_region and type_region != category_region:
        raise TryOnError('Vision 상품 종류와 카테고리가 일치하지 않습니다.', 422)
    region = type_region or category_region
    if region == 'accessory_group':
        raise TryOnError('액세서리의 구체적인 종류를 확인할 수 없습니다.', 422)
    return region


def _jpeg(data: bytes, max_side: int, quality: int) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP') or max(source.size) > 8000:
                raise ValueError('unsupported image')
            source.load()
            if 'A' in source.getbands():
                canvas = Image.new('RGBA', source.size, 'white')
                canvas.alpha_composite(source.convert('RGBA'))
                image = canvas.convert('RGB')
            else:
                image = source.convert('RGB')
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise TryOnError('가상 착용 입력 이미지를 처리할 수 없습니다.', 422) from exc


def _product_image(url: str) -> bytes:
    cached = cached_catalog_image(url)
    if cached is not None:
        logger.info('Try-on product_image_source=cache')
        return cached
    try:
        parsed = urlparse(url)
        allowed_url = (parsed.scheme == 'https' and parsed.hostname == 'image.msscdn.net'
                       and parsed.port in (None, 443))
    except ValueError:
        allowed_url = False
    if not allowed_url:
        raise TryOnError('허용되지 않은 상품 이미지 주소입니다.', 422)
    if not _safe_image_host(url):
        raise TryOnError('상품 이미지 서버의 공개 IP를 확인할 수 없습니다.', 502)
    try:
        with httpx.stream('GET', url, timeout=10.0, follow_redirects=False) as response:
            mime = response.headers.get('content-type', '').split(';')[0].strip().lower()
            if mime == 'image/jpg':
                mime = 'image/jpeg'
            if response.status_code != 200:
                raise TryOnError(f'무신사 상품 이미지 서버가 HTTP {response.status_code}을 반환했습니다.', 502)
            if mime not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise TryOnError(f'상품 이미지 Content-Type을 지원하지 않습니다: {mime[:60] or "없음"}', 502)
            try:
                content_length = int(response.headers.get('content-length', '0'))
            except ValueError as exc:
                raise TryOnError('상품 이미지 Content-Length가 올바르지 않습니다.', 502) from exc
            if content_length > MAX_PRODUCT_BYTES:
                raise TryOnError('상품 이미지가 너무 큽니다.', 413)
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_PRODUCT_BYTES:
                    raise TryOnError('상품 이미지가 너무 큽니다.', 413)
                chunks.append(chunk)
        if not chunks:
            raise TryOnError('상품 이미지가 비어 있습니다.', 502)
        data = b''.join(chunks)
        try:
            with Image.open(io.BytesIO(data)) as source:
                actual = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}.get(source.format)
                if actual != mime:
                    raise TryOnError('상품 이미지 내용과 Content-Type이 일치하지 않습니다.', 502)
                if max(source.size) > 8000:
                    raise TryOnError('상품 이미지 해상도가 너무 큽니다.', 413)
                source.verify()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise TryOnError('상품 이미지 디코딩에 실패했습니다.', 502) from exc
        cache_catalog_image(url, data)
        logger.info('Try-on product_image_source=download')
        return data
    except httpx.TimeoutException as exc:
        raise TryOnError('상품 이미지 다운로드 시간이 초과되었습니다.', 504) from exc
    except httpx.HTTPError as exc:
        raise TryOnError('상품 이미지 다운로드 중 네트워크 오류가 발생했습니다.', 502) from exc


def _settings(width: int, height: int) -> tuple[str, str, str]:
    model = os.getenv('OPENAI_TRYON_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL
    quality = os.getenv('OPENAI_TRYON_QUALITY', 'medium').strip() or 'medium'
    size = os.getenv('OPENAI_TRYON_SIZE', '').strip()
    if not size:
        size = '1024x1536' if height > width * 1.1 else '1536x1024' if width > height * 1.1 else '1024x1024'
    if model not in MODELS or quality not in QUALITIES or size not in SIZES:
        raise TryOnError('OPENAI_TRYON_MODEL, OPENAI_TRYON_QUALITY 또는 OPENAI_TRYON_SIZE 설정을 확인하세요.', 503)
    return model, quality, size


def _generate(person_bytes: bytes, product: dict, selected: dict,
              box: list[float] | None, width: int, height: int) -> dict:
    started = time.monotonic()
    if not os.getenv('OPENAI_API_KEY', '').strip():
        raise TryOnError('backend/.env에 OPENAI_API_KEY를 설정하세요.', 503)
    model, quality, size = _settings(width, height)
    catalog_bytes = _product_image(product['image_url'])
    person_input = _jpeg(person_bytes, 1536, 88)
    catalog_input = _jpeg(catalog_bytes, 1024, 90)
    prompt = build_tryon_prompt(product['product_name'], product.get('brand'),
                                selected['product_type'], selected.get('description') or '',
                                box, width, height,
                                region=_selected_region(selected))
    try:
        client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=120.0, max_retries=0)
        response = client.images.edit(
            model=model,
            image=[('person.jpg', person_input, 'image/jpeg'),
                   ('product.jpg', catalog_input, 'image/jpeg')],
            prompt=prompt, n=1, quality=quality, size=size,
            output_format='jpeg', output_compression=85,
        )
    except APITimeoutError as exc:
        raise TryOnError('가상 착용 이미지 생성 시간이 초과되었습니다.', 504) from exc
    except APIConnectionError as exc:
        raise TryOnError('OpenAI 이미지 편집 서비스에 연결할 수 없습니다.', 502) from exc
    except APIStatusError as exc:
        logger.warning('Try-on OpenAI status=%s model=%s', exc.status_code, model)
        raise TryOnError('OpenAI 이미지 편집 요청에 실패했습니다.', 429 if exc.status_code == 429 else 502) from exc
    except Exception as exc:
        logger.error('Try-on edit error_type=%s', type(exc).__name__)
        raise TryOnError('가상 착용 이미지를 생성하지 못했습니다.', 502) from exc
    encoded = response.data[0].b64_json if response.data else None
    if not encoded:
        raise TryOnError('OpenAI가 가상 착용 이미지를 반환하지 않았습니다.', 502)
    try:
        edited = base64.b64decode(encoded, validate=True)
        if len(edited) > MAX_RESULT_BYTES:
            raise TryOnError('생성된 이미지가 너무 큽니다.', 502)
        with Image.open(io.BytesIO(edited)) as opened:
            if opened.format != 'JPEG':
                raise TryOnError('생성된 이미지 형식이 올바르지 않습니다.', 502)
            opened.verify()
    except (binascii.Error, OSError, UnidentifiedImageError) as exc:
        raise TryOnError('생성된 이미지를 확인할 수 없습니다.', 502) from exc
    elapsed_ms = round((time.monotonic() - started) * 1000)
    usage = response.usage
    logger.info('Try-on model=%s elapsed_ms=%s result_bytes=%s', model, elapsed_ms, len(edited))
    return {'status': 'success', 'product_id': product['product_id'],
            'image_data_url': 'data:image/jpeg;base64,' + encoded,
            'model': model, 'elapsed_ms': elapsed_ms,
            'usage': ({'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens,
                       'total_tokens': usage.total_tokens} if usage else None)}


def generate_tryon(person_bytes: bytes, product: dict, selected: dict,
                   box: list[float] | None, width: int, height: int) -> dict:
    selected_region = _selected_region(selected)
    if selected_region is None:
        raise TryOnError('선택한 상품 종류는 가상 착용에 사용할 수 없습니다.', 422)
    key = hashlib.sha256(person_bytes + json.dumps(
        {'product': product, 'selected': selected, 'box': box},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _lock:
        pending = _pending.get(key)
        if pending is None:
            pending = Future()
            _pending[key] = pending
            owner = True
        else:
            owner = False
    if not owner:
        try:
            return pending.result(timeout=150)
        except TimeoutError as exc:
            raise TryOnError('진행 중인 가상 착용 생성 시간이 초과되었습니다.', 504) from exc
    try:
        result = _generate(person_bytes, product, selected, box, width, height)
        pending.set_result(result)
        return result
    except Exception as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)
