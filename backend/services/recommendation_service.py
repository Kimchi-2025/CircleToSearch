"""One-call visual ranking of Musinsa results against the existing product crop."""

import base64
import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import socket
import time
from collections import OrderedDict
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Lock
from typing import Literal
from urllib.parse import urlparse

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from services.musinsa_service import SearchError, _candidate_score, search_products
from services.vision_service import DEFAULT_MODEL

logger = logging.getLogger('uvicorn.error')
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 30
MAX_RESULTS = 10
IMAGE_PREPARATION_TIMEOUT = 18
_cache: OrderedDict[str, dict] = OrderedDict()
_pending: dict[str, Future] = {}
_lock = Lock()
_thumbnail_cache: OrderedDict[str, Image.Image] = OrderedDict()
_catalog_image_cache: OrderedDict[str, bytes] = OrderedDict()
_thumbnail_lock = Lock()


class ComparedCandidate(BaseModel):
    product_id: str
    similarity_grade: Literal['very_similar', 'similar', 'somewhat_similar', 'not_similar']


class ComparisonOutput(BaseModel):
    recommendations: list[ComparedCandidate]


class RecommendationError(Exception):
    def __init__(self, message: str, status: str, http_status: int = 502, diagnostics=None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status
        self.diagnostics = diagnostics


def _safe_image_host(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'image.msscdn.net' or parsed.port not in (None, 443):
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def cached_catalog_image(url: str) -> bytes | None:
    """Original, validated catalog bytes from a successful comparison download."""
    with _thumbnail_lock:
        data = _catalog_image_cache.get(url)
        if data is not None:
            _catalog_image_cache.move_to_end(url)
        return data


def cache_catalog_image(url: str, data: bytes) -> None:
    if len(data) > 5 * 1024 * 1024:
        return
    with _thumbnail_lock:
        _catalog_image_cache[url] = data
        _catalog_image_cache.move_to_end(url)
        while (len(_catalog_image_cache) > 32 or
               sum(map(len, _catalog_image_cache.values())) > 32 * 1024 * 1024):
            _catalog_image_cache.popitem(last=False)


def _thumbnail(url):
    with _thumbnail_lock:
        cached = _thumbnail_cache.get(url)
        if cached is not None:
            _thumbnail_cache.move_to_end(url)
            return cached
    if not _safe_image_host(url):
        return None
    try:
        with httpx.stream('GET', url, timeout=6.0, follow_redirects=False) as response:
            mime = response.headers.get('content-type', '').split(';')[0].strip().lower()
            if response.status_code != 200 or not mime.startswith('image/'):
                return None
            if int(response.headers.get('content-length', '0')) > MAX_IMAGE_BYTES:
                return None
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
        original_bytes = b''.join(chunks)
        with Image.open(io.BytesIO(original_bytes)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP') or max(source.size) > 3000:
                return None
            actual_mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}[source.format]
            image = ImageOps.contain(source.convert('RGB'), (185, 185))
            image.load()
            with _thumbnail_lock:
                _thumbnail_cache[url] = image
                _thumbnail_cache.move_to_end(url)
                if len(_thumbnail_cache) > 128:
                    _thumbnail_cache.popitem(last=False)
            if mime == actual_mime:
                cache_catalog_image(url, original_bytes)
            return image
    except (httpx.HTTPError, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def _comparison_candidates(candidates, selected):
    """Reduce only when title signals are strong; preserve query diversity."""
    if len(candidates) <= 20:
        return candidates
    ranked = sorted(enumerate(candidates),
                    key=lambda pair: (-_candidate_score(pair[1], selected), pair[0]))
    scores = [_candidate_score(product, selected) for _, product in ranked]
    positive = sum(score > 0 for score in scores)
    spread = scores[0] - scores[-1]
    target = 20 if positive >= 15 and spread >= 2 else 24 if positive >= 15 else MAX_CANDIDATES
    if len(candidates) <= target:
        return candidates
    # Diversify only among titles close to the best score; keep clear matches first.
    floor = max(0, scores[0] - 1)
    groups = {}
    for index, product in ranked:
        if _candidate_score(product, selected) >= floor:
            groups.setdefault(product.get('matched_search_keyword') or '', []).append(index)
    chosen = set()
    while len(chosen) < target and any(groups.values()):
        for group in groups.values():
            if group and len(chosen) < target:
                chosen.add(group.pop(0))
    for index, _ in ranked:
        if len(chosen) == target:
            break
        chosen.add(index)
    return [product for index, product in ranked if index in chosen]


def _contact_sheet(candidates, timings=None):
    started = time.monotonic()
    products = candidates[:MAX_CANDIDATES]
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures = {url: pool.submit(_thumbnail, url) for url in
                   dict.fromkeys(product.get('image_url') for product in products)
                   if isinstance(url, str) and url}
        done, _ = wait(futures.values(), timeout=IMAGE_PREPARATION_TIMEOUT)
        loaded = {}
        for url, future in futures.items():
            if future in done:
                try:
                    loaded[url] = future.result()
                except Exception:
                    loaded[url] = None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    download_ms = round((time.monotonic() - started) * 1000)
    if timings is not None:
        timings['image_download_elapsed_ms'] = download_ms
    usable = [(product, loaded[product['image_url']]) for product in products
              if product.get('image_url') in loaded and loaded[product['image_url']] is not None]
    if not usable:
        logger.info('Recommendation contact_sheet_ms=%s usable_images=0',
                    round((time.monotonic() - started) * 1000))
        if timings is not None:
            timings['contact_sheet_build_elapsed_ms'] = 0
        return None, []
    columns, cell_width, cell_height = 5, 210, 220
    rows = math.ceil(len(usable) / columns)
    sheet = Image.new('RGB', (columns * cell_width, rows * cell_height), 'white')
    draw = ImageDraw.Draw(sheet)
    for index, (product, thumb) in enumerate(usable):
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 5))
        draw.text((x + 5, y + 195), f"#{index + 1} ID {product['product_id']}", fill='black')
    output = io.BytesIO()
    sheet.save(output, format='JPEG', quality=78, optimize=True)
    sheet_bytes = output.getvalue()
    build_ms = round((time.monotonic() - started) * 1000) - download_ms
    if timings is not None:
        timings['contact_sheet_build_elapsed_ms'] = build_ms
        timings['contact_sheet_width_px'] = sheet.width
        timings['contact_sheet_height_px'] = sheet.height
        timings['contact_sheet_image_bytes'] = len(sheet_bytes)
    logger.info('Recommendation image_download_ms=%s contact_sheet_build_ms=%s usable_images=%s',
                download_ms, build_ms, len(usable))
    return sheet_bytes, [product for product, _ in usable]


def _small_crop(crop_bytes, dimensions=None):
    with Image.open(io.BytesIO(crop_bytes)) as source:
        image = source.convert('RGB')
        image.thumbnail((768, 768), Image.Resampling.LANCZOS)
        if dimensions is not None:
            dimensions['crop_width_px'], dimensions['crop_height_px'] = image.size
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=85, optimize=True)
    return output.getvalue()


def _data_url(data):
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')


def compare_candidates(crop_bytes, selected_object, candidates, model, include_diagnostics=False):
    sheet_started = time.monotonic()
    timings = {}
    sheet_bytes, usable = _contact_sheet(candidates, timings=timings)
    sheet_ms = round((time.monotonic() - sheet_started) * 1000)
    if not usable:
        raise RecommendationError('비교 가능한 무신사 상품 이미지가 없습니다.', 'no_comparable_images')
    description = selected_object.get('description') or ''
    features = {key: selected_object.get(key) for key in (
        'category', 'product_type', 'dominant_color', 'secondary_colors', 'pattern',
        'visible_text_or_logo', 'material_appearance', 'fit_or_shape', 'style')}
    lines = [f"#{index + 1} = ID {item['product_id']} | {item['brand'] or ''} | {item['product_name']}"
             for index, item in enumerate(usable)]
    prompt = ("Compare the first image (the selected fashion product crop) with the numbered "
              "Musinsa product thumbnails in the second image. The crop may include background. "
              "Use the existing visual analysis below; do not re-identify unrelated objects. "
              "Treat product type as the first requirement. Then compare exact visible color, "
              "graphics, lettering/logo (including text omitted from search queries), pattern, "
              "fit, silhouette, material appearance and overall style. Exclude clearly different "
              "types. Grade each returned item: very_similar if type, color and design closely match; "
              "similar if type and most important color/design features match; somewhat_similar if "
              "type and some important features match and the item is still useful to compare; "
              "not_similar if type or core features clearly differ. Prioritize very_similar and similar. "
              "If fewer than five qualify, include useful somewhat_similar items of the same product "
              "type with some matching major visual features. Aim for 5-10 useful items, but return "
              "fewer than five if suitable items are lacking. Exclude unrelated items; best grades first. "
              "Return only IDs from the mapping and similarity grades, without explanations. "
              "Price is not a similarity signal.\n"
              f"Description: {description}\nFeatures: {json.dumps(features, ensure_ascii=False)}\n"
              "Number-to-ID mapping:\n" + '\n'.join(lines))
    started = time.monotonic()
    crop_dimensions = {}
    try:
        client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=45.0, max_retries=0)
        crop_input = _small_crop(crop_bytes, crop_dimensions)
        response = client.responses.parse(
            model=model,
            instructions='You are a careful visual fashion product matcher. Return only truly similar catalog items.',
            input=[{'role': 'user', 'content': [
                {'type': 'input_text', 'text': prompt},
                {'type': 'input_image', 'image_url': _data_url(crop_input), 'detail': 'high'},
                {'type': 'input_image', 'image_url': _data_url(sheet_bytes), 'detail': 'high'},
            ]}],
            text_format=ComparisonOutput,
            store=False,
            max_output_tokens=1600,
        )
    except APITimeoutError as exc:
        raise RecommendationError('OpenAI 유사도 비교 시간이 초과됐습니다.', 'vision_error', 504) from exc
    except APIConnectionError as exc:
        raise RecommendationError('OpenAI 비교 서비스에 연결하지 못했습니다.', 'vision_error') from exc
    except APIStatusError as exc:
        logger.warning('Recommendation OpenAI status=%s model=%s', exc.status_code, model)
        raise RecommendationError('OpenAI 유사도 비교에 실패했습니다.', 'vision_error',
                                  429 if exc.status_code == 429 else 502) from exc
    except Exception as exc:
        logger.error('Recommendation comparison error_type=%s', type(exc).__name__)
        raise RecommendationError('유사도 비교 결과를 처리하지 못했습니다.', 'vision_error') from exc
    finally:
        logger.info('Recommendation openai_comparison_ms=%s',
                    round((time.monotonic() - started) * 1000))
    if response.output_parsed is None:
        raise RecommendationError('OpenAI가 비교 결과를 반환하지 않았습니다.', 'vision_error')
    by_id = {item['product_id']: item for item in usable}
    picked, seen = [], set()
    excluded = {'unknown_id': 0, 'duplicate_id': 0, 'not_similar': 0, 'over_limit': 0}
    grade_order = {'very_similar': 0, 'similar': 1, 'somewhat_similar': 2, 'not_similar': 3}
    for item in sorted(response.output_parsed.recommendations,
                       key=lambda candidate: grade_order[candidate.similarity_grade]):
        if item.product_id not in by_id:
            excluded['unknown_id'] += 1
            continue
        if item.similarity_grade == 'not_similar':
            excluded['not_similar'] += 1
            continue
        if item.product_id in seen:
            excluded['duplicate_id'] += 1
            continue
        seen.add(item.product_id)
        if len(picked) >= MAX_RESULTS:
            excluded['over_limit'] += 1
            continue
        picked.append({**by_id[item.product_id], 'rank': len(picked) + 1,
                       'similarity_grade': item.similarity_grade})
    usage = response.usage
    elapsed = round((time.monotonic() - started) * 1000)
    logger.info('Recommendation model=%s input_tokens=%s output_tokens=%s',
                response.model or model,
                usage.input_tokens if usage else None, usage.output_tokens if usage else None)
    usage_info = {
        'model': response.model or model,
        'input_tokens': usage.input_tokens if usage else None,
        'output_tokens': usage.output_tokens if usage else None,
        'total_tokens': usage.total_tokens if usage else None,
    }
    diagnostics = {'input_candidate_count': len(usable),
                   'selected_count': len(response.output_parsed.recommendations),
                   'final_count': len(picked), 'excluded_count': sum(excluded.values()),
                   'excluded_reasons': excluded, 'elapsed_ms': elapsed,
                   'input_image_count': 2,
                   'crop_image_bytes': len(crop_input),
                   'crop_width_px': crop_dimensions['crop_width_px'],
                   'crop_height_px': crop_dimensions['crop_height_px'],
                   'contact_sheet_image_bytes': len(sheet_bytes),
                   'contact_sheet_width_px': timings.get('contact_sheet_width_px'),
                   'contact_sheet_height_px': timings.get('contact_sheet_height_px'),
                   'input_image_bytes': len(crop_input) + len(sheet_bytes),
                   'output_tokens': usage.output_tokens if usage else None,
                   'contact_sheet_elapsed_ms': sheet_ms,
                   'image_download_elapsed_ms': timings.get('image_download_elapsed_ms', 0),
                   'contact_sheet_build_elapsed_ms': timings.get('contact_sheet_build_elapsed_ms', 0)}
    return (picked, elapsed, usage_info, diagnostics) if include_diagnostics else (picked, elapsed, usage_info)


def recommend(crop_bytes, selected_object, keywords):
    started = time.monotonic()
    if not os.getenv('OPENAI_API_KEY', '').strip():
        raise RecommendationError('backend/.env에 OPENAI_API_KEY를 설정하세요.', 'vision_error', 503)
    model = os.getenv('OPENAI_VISION_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL
    unique_keywords = list(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))[:4]
    if not unique_keywords:
        raise RecommendationError('검색어가 없습니다.', 'invalid_request', 422)
    key = hashlib.sha256(crop_bytes + json.dumps(
        {'object': selected_object, 'keywords': unique_keywords, 'model': model},
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            logger.info('Recommendation cache_hit total_elapsed_ms=%s',
                        round((time.monotonic() - started) * 1000))
            return {**cached, 'cached': True}
        pending = _pending.get(key)
        if pending is None:
            pending = Future()
            _pending[key] = pending
            owner = True
        else:
            owner = False
    if not owner:
        try:
            result = pending.result(timeout=180)
            logger.info('Recommendation shared_request total_elapsed_ms=%s',
                        round((time.monotonic() - started) * 1000))
            return {**result, 'cached': True}
        except FutureTimeoutError as exc:
            raise RecommendationError('진행 중인 검색 시간이 초과됐습니다.', 'search_error', 504) from exc
    search_diagnostics = None
    try:
        candidates, searched, search_ms, search_diagnostics = search_products(
            unique_keywords, selected_object, include_diagnostics=True)
        if not candidates:
            result = {'search_status': 'no_candidates', 'searched_keywords': searched,
                      'candidate_count': 0, 'recommended_count': 0, 'recommendations': [],
                      'search_elapsed_ms': search_ms, 'comparison_elapsed_ms': 0, 'vision_usage': None}
            vision_diagnostics = {'input_candidate_count': 0, 'selected_count': 0,
                                  'final_count': 0, 'excluded_count': 0, 'excluded_reasons': {},
                                  'elapsed_ms': 0, 'contact_sheet_elapsed_ms': 0,
                                  'preselected_candidate_count': 0,
                                  'image_download_elapsed_ms': 0,
                                  'contact_sheet_build_elapsed_ms': 0}
        else:
            comparison_candidates = _comparison_candidates(candidates, selected_object)
            logger.info('Recommendation collected_candidates=%s preselected_candidates=%s',
                        len(candidates), len(comparison_candidates))
            picked, comparison_ms, usage, vision_diagnostics = compare_candidates(
                crop_bytes, selected_object, comparison_candidates, model, include_diagnostics=True)
            vision_diagnostics['preselected_candidate_count'] = len(comparison_candidates)
            result = {'search_status': 'success', 'searched_keywords': searched,
                      'candidate_count': len(candidates), 'recommended_count': len(picked),
                      'recommendations': picked, 'search_elapsed_ms': search_ms,
                      'comparison_elapsed_ms': comparison_ms, 'vision_usage': usage}
        total_ms = round((time.monotonic() - started) * 1000)
        other_ms = max(0, total_ms - search_ms - comparison_ms -
                       vision_diagnostics.get('contact_sheet_elapsed_ms', 0))
        result['search_diagnostics'] = {**search_diagnostics,
            'candidate_count': result['candidate_count'],
            'recommended_count': result['recommended_count'],
            'vision_comparison': vision_diagnostics,
            'other_elapsed_ms': other_ms,
            'total_elapsed_ms': total_ms}
        with _lock:
            _cache[key] = result
            _cache.move_to_end(key)
            if len(_cache) > 16:
                _cache.popitem(last=False)
        pending.set_result(result)
        return {**result, 'cached': False}
    except SearchError as exc:
        diagnostics = {**(exc.diagnostics or {}), 'recommended_count': 0,
                       'vision_comparison': None,
                       'total_elapsed_ms': round((time.monotonic() - started) * 1000)}
        error = RecommendationError(str(exc), exc.status,
                                    503 if exc.status == 'access_blocked' else 502, diagnostics)
        pending.set_exception(error)
        raise error from exc
    except RecommendationError as exc:
        if search_diagnostics is not None:
            exc.diagnostics = {**search_diagnostics, 'recommended_count': None,
                'vision_comparison': None,
                'total_elapsed_ms': round((time.monotonic() - started) * 1000)}
        pending.set_exception(exc)
        raise
    except Exception as exc:
        pending.set_exception(exc)
        raise
    finally:
        logger.info('Recommendation total_elapsed_ms=%s', round((time.monotonic() - started) * 1000))
        with _lock:
            _pending.pop(key, None)
