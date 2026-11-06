"""Public Musinsa search page parser, adapted from musinsa_final_30_gallery.py."""

import logging
import os
import re
import time
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

SELECTOR = 'a.gtm-view-item-list[data-item-id][data-item-list-id="goods_list"]'
MAX_PRODUCTS = 30
MAX_POOL = 120  # Four result pages, at most 30 cards each; only 30 reach Vision.
ENOUGH_PRODUCTS = 20
MAX_PER_QUERY = 18
NAVIGATION_TIMEOUT_MS = int(os.getenv('MUSINSA_NAVIGATION_TIMEOUT_MS', '20000'))
SELECTOR_TIMEOUT_MS = max(1, min(int(os.getenv('MUSINSA_SELECTOR_TIMEOUT_MS', '4500')), 10000))
BODY_TIMEOUT_MS = max(1, min(int(os.getenv('MUSINSA_BODY_TIMEOUT_MS', '3000')), 8000))
ACCESS_DENIED_TEXT = ('접속이 일시적으로 제한', '비정상적인 접근', 'Access Denied', 'captcha')
logger = logging.getLogger('uvicorn.error')

# Only remove an unmistakably different product type. The image comparison
# decides finer distinctions such as prints, exact color, and shoe silhouette.
PRODUCT_FAMILIES = (
    ('tshirt', ('티셔츠', '반팔티', '긴팔티', '반팔 티', '긴팔 티', '그래픽티')),
    ('sweatshirt', ('맨투맨', '스웨트셔츠', '스웻셔츠')),
    ('hoodie', ('후드티', '후디', '후드 집업', '후드집업')),
    ('shirt', ('셔츠', '남방', '블라우스')),
    ('knit', ('니트', '스웨터', '가디건')),
    ('outer', ('자켓', '재킷', '점퍼', '코트', '패딩', '블레이저', '바람막이')),
    ('shorts', ('반바지', '쇼츠', '숏팬츠')),
    ('pants', ('바지', '팬츠', '슬랙스', '청바지', '데님', '조거')),
    ('skirt', ('스커트', '치마')),
    ('dress', ('원피스', '드레스')),
    ('shoes', ('신발', '스니커즈', '운동화', '슈즈', '부츠', '로퍼', '구두', '샌들', '슬리퍼', '힐')),
    ('bag', ('가방', '백팩', '숄더백', '토트백', '크로스백', '파우치', '핸드백')),
    ('watch', ('시계', '워치')),
    ('belt', ('벨트', '허리띠')),
    ('glasses', ('안경', '선글라스', '아이웨어')),
)


class SearchError(Exception):
    def __init__(self, message: str, status: str = 'search_error', diagnostics=None):
        super().__init__(message)
        self.status = status
        self.diagnostics = diagnostics


def as_int(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return int(digits) if digits else None


def full_url(value, domain, allowed_hosts):
    if not value or str(value).startswith('data:'):
        return None
    result = urljoin(domain, str(value))
    parsed = urlparse(result)
    return result if parsed.scheme == 'https' and parsed.hostname in allowed_hosts else None


def product_family(name):
    value = str(name or '').lower()
    return next((family for family, terms in PRODUCT_FAMILIES
                 if any(term in value for term in terms)), None)


def _named_feature(value, groups):
    text = str(value or '').lower()
    return next((name for name, words in groups if any(word in text for word in words)), None)


COLORS = (
    ('navy', ('네이비', '남색', 'navy')), ('black', ('블랙', '검정', 'black')),
    ('white', ('화이트', '흰색', 'white')), ('gray', ('그레이', '회색', 'gray')),
    ('blue', ('블루', '파랑', 'blue')), ('red', ('레드', '빨강', 'red')),
    ('green', ('그린', '초록', 'green')), ('beige', ('베이지', 'beige')),
)
DESIGNS = (
    ('plain', ('무지', '솔리드')), ('lettering', ('레터링', '영문', 'lettering')),
    ('graphic', ('그래픽', 'graphic')), ('print', ('프린팅', '프린트', 'print')),
    ('logo', ('로고', 'logo')), ('embroidery', ('자수', 'embroidery')),
)


def _candidate_score(product, selected):
    name = product['product_name']
    wanted_color = _named_feature(selected.get('dominant_color'), COLORS)
    named_color = _named_feature(name, COLORS)
    wanted_design = _named_feature(selected.get('pattern'), DESIGNS)
    named_design = _named_feature(name, DESIGNS)
    score = 0
    if wanted_color and named_color:
        score += 2 if wanted_color == named_color else -3
    if wanted_design and named_design:
        if wanted_design == named_design:
            score += 1
        elif 'plain' in (wanted_design, named_design):
            score -= 2
    return score


def _ranked_candidates(products, selected):
    ranked = [product for _, product in sorted(enumerate(products),
              key=lambda pair: (-_candidate_score(pair[1], selected), pair[0]))]
    sources = {item['matched_search_keyword'] for item in ranked}
    if len(sources) < 2:
        return ranked[:MAX_PRODUCTS]
    # Reserve room for another query only when its title is reasonably relevant.
    threshold = max(0, _candidate_score(ranked[0], selected) - 2)
    chosen, source_counts, selected_ids = [], {}, set()
    for product in ranked:
        source = product['matched_search_keyword']
        if (_candidate_score(product, selected) < threshold or
                source_counts.get(source, 0) >= MAX_PER_QUERY):
            continue
        chosen.append(product)
        selected_ids.add(product['product_id'])
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(chosen) == MAX_PRODUCTS:
            return chosen
    for product in ranked:
        if product['product_id'] not in selected_ids:
            chosen.append(product)
            if len(chosen) == MAX_PRODUCTS:
                break
    return chosen


def _needs_more_queries(products, selected, completed_queries):
    ranked = _ranked_candidates(products, selected)
    if len(ranked) < 10:
        return True
    sources = {item['matched_search_keyword'] for item in ranked}
    signatures = {(_named_feature(item['product_name'], COLORS),
                   _named_feature(item['product_name'], DESIGNS)) for item in ranked}
    strong = sum(_candidate_score(item, selected) >= 0 for item in ranked)
    if len(ranked) >= ENOUGH_PRODUCTS and strong >= ENOUGH_PRODUCTS:
        return len(signatures) < (3 if len(sources) == 1 else 2)
    # After two useful searches, avoid extra navigation for a small gain.
    return not (completed_queries >= 2 and len(sources) >= 2 and
                len(signatures) >= 2 and strong >= max(10, len(ranked) // 2))


def _search_diagnostics(keywords, stats, candidate_count, elapsed_ms):
    return {'executed_query_count': sum(item['status'] != 'skipped' for item in stats),
            'candidate_count': candidate_count,
            'query_stats': stats + [dict(keyword=keyword, status='skipped', collected_count=0,
                                         added_count=0, elapsed_ms=0)
                                    for keyword in keywords[len(stats):]],
            'search_elapsed_ms': elapsed_ms}


def extract_products(markup, keyword, limit):
    soup = BeautifulSoup(markup, 'html.parser')
    items, seen = [], set()
    for image_link in soup.select(SELECTOR):
        pid = str(image_link.get('data-item-id') or '')
        if not pid.isdigit() or pid in seen:
            continue
        card = image_link.find_parent('div', class_=lambda c: bool(c) and 'UIItemContainerStyle' in c)
        if card is None:
            continue
        title = card.select_one('a.gtm-select-item[data-item-id][aria-label*="상품상세"]')
        if title is None or not title.get_text(' ', strip=True):
            continue
        product_url = full_url(image_link.get('href') or title.get('href'),
                               'https://www.musinsa.com', {'www.musinsa.com', 'musinsa.com'})
        if not product_url or not re.fullmatch(r'/(?:products|app/goods)/\d+', urlparse(product_url).path):
            continue
        if urlparse(product_url).path.rsplit('/', 1)[-1] != pid:
            continue
        brand = card.select_one('a[href*="/brand/"]')
        img = image_link.find('img')
        img_url = (img.get('src') or img.get('data-src') or '') if img else ''
        if str(img_url).startswith('data:'):
            img_url = (img.get('data-src') or '') if img else ''
        price_area = card.select_one('[class*="GoodsItem-"][class*="__Gt-sc-"]')
        prices = re.findall(r'([\d,]+)\s*원', price_area.get_text(' ', strip=True) if price_area else '')
        items.append({
            'product_id': pid,
            'brand': brand.get_text(' ', strip=True) if brand else None,
            'product_name': title.get_text(' ', strip=True),
            'price': as_int(prices[-1]) if prices else as_int(image_link.get('data-price')),
            'image_url': full_url(img_url, 'https://image.msscdn.net', {'image.msscdn.net'}),
            'product_url': product_url,
            'matched_search_keyword': keyword,
        })
        seen.add(pid)
        if len(items) >= limit:
            break
    return items


def search_products(keywords, product_type=None, include_diagnostics=False):
    """Reuse one Chromium page and search sequentially until enough unique products."""
    started = time.monotonic()
    products, seen, searched, stats = [], set(), [], []
    selected = product_type if isinstance(product_type, dict) else {'product_type': product_type}
    selected_family = product_family(selected.get('product_type') or selected.get('category'))
    stage, stage_started, keyword_index, http_status = 'playwright_start', started, 0, None
    failure_logged = False
    logger.info('Musinsa search_start navigation_timeout_ms=%s', NAVIGATION_TIMEOUT_MS)

    def log_failure(exc):
        detail = re.sub(r'https?://\S+', '[redacted_url]', str(exc).splitlines()[0])[:160] if str(exc) else ''
        closed = 'browser has been closed' in detail.lower() or 'browser closed' in detail.lower()
        category = ('navigation_timeout' if stage == 'navigation' and isinstance(exc, PlaywrightTimeoutError)
                    else 'browser_closed' if closed or stage == 'browser_close'
                    else 'http_status' if stage == 'http_status'
                    else 'extraction_error' if stage == 'extraction'
                    else 'other_exception')
        logger.warning('Musinsa search_failure stage=%s category=%s keyword_index=%s '
                       'stage_elapsed_ms=%s http_status=%s error_type=%s detail=%s',
                       stage, category, keyword_index, round((time.monotonic() - stage_started) * 1000),
                       http_status, type(exc).__name__, detail)

    try:
        with sync_playwright() as playwright:
            stage, stage_started = 'browser_launch', time.monotonic()
            browser = playwright.chromium.launch(headless=True)
            try:
                stage, stage_started = 'context_create', time.monotonic()
                context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='ko-KR')
                stage, stage_started = 'route_setup', time.monotonic()
                context.route('**/*', lambda route: route.abort() if route.request.resource_type in
                              ('image', 'font', 'media') else route.continue_())
                stage, stage_started = 'page_create', time.monotonic()
                page = context.new_page()
                for index, keyword in enumerate(keywords[:4]):
                    if index and (len(products) >= MAX_POOL or
                                  not _needs_more_queries(products, selected, index)):
                        break
                    url = 'https://www.musinsa.com/search/goods?' + urlencode({'keyword': keyword})
                    searched.append(keyword)
                    keyword_started, before = time.monotonic(), len(products)
                    collected = type_filtered = duplicate = capacity_skipped = 0
                    query_status = 'executed'
                    try:
                        keyword_index, http_status = index + 1, None
                        stage, stage_started = 'navigation', time.monotonic()
                        response = page.goto(url, wait_until='domcontentloaded', timeout=NAVIGATION_TIMEOUT_MS)
                        status = response.status if response else None
                        stage, stage_started, http_status = 'http_status', time.monotonic(), status
                        if status in (403, 429):
                            raise SearchError('무신사 접근이 제한됐습니다. 검색을 중단합니다.', 'access_blocked')
                        if status != 200:
                            raise SearchError(f'무신사 검색 페이지 응답 오류: HTTP {status}')
                        stage, stage_started = 'selector_wait', time.monotonic()
                        selector_wait_failed = False
                        selector = page.locator(SELECTOR)
                        if selector.count() == 0:
                            try:
                                selector.first.wait_for(state='attached', timeout=SELECTOR_TIMEOUT_MS)
                            except Exception as exc:
                                selector_wait_failed = True
                                logger.info('Musinsa selector_wait_failed keyword_index=%s elapsed_ms=%s '
                                            'timeout_ms=%s error_type=%s',
                                            keyword_index, round((time.monotonic() - stage_started) * 1000),
                                            SELECTOR_TIMEOUT_MS, type(exc).__name__)
                                pass  # Empty results are possible; inspect the page before continuing.
                        stage, stage_started = 'body_read', time.monotonic()
                        try:
                            body = page.locator('body').inner_text(timeout=BODY_TIMEOUT_MS)
                        except PlaywrightTimeoutError:
                            logger.info('Musinsa body_read_fallback keyword_index=%s method=text_content', keyword_index)
                            body = page.locator('body').text_content(timeout=BODY_TIMEOUT_MS) or ''
                        stage, stage_started = 'access_check', time.monotonic()
                        if selector_wait_failed:
                            logger.info('Musinsa selector_missing_dom keyword_index=%s body_chars=%s '
                                        'dom_state=%s http_status=%s', keyword_index, len(body),
                                        'empty_body' if not body.strip() else 'body_without_product_selector', http_status)
                        if any(text.lower() in body.lower() for text in ACCESS_DENIED_TEXT):
                            raise SearchError('무신사 접근 제한 또는 CAPTCHA가 감지됐습니다.', 'access_blocked')
                        stage, stage_started = 'extraction', time.monotonic()
                        extracted = extract_products(page.content(), keyword, MAX_PRODUCTS)
                        collected = len(extracted)
                        if not collected:
                            logger.warning('Musinsa extraction_empty keyword_index=%s elapsed_ms=%s '
                                           'http_status=%s body_chars=%s',
                                           keyword_index, round((time.monotonic() - stage_started) * 1000),
                                           http_status, len(body))
                        stage, stage_started = 'filter', time.monotonic()
                        for product in extracted:
                            family = product_family(product['product_name'])
                            if selected_family and family and family != selected_family:
                                type_filtered += 1
                                continue
                            key = product['product_id'] or product['product_url']
                            if key in seen:
                                duplicate += 1
                                continue
                            seen.add(key)
                            if len(products) < MAX_POOL:
                                products.append(product)
                            else:
                                capacity_skipped += 1
                    except Exception as exc:
                        query_status = 'failed'
                        log_failure(exc)
                        failure_logged = True
                        raise
                    finally:
                        elapsed_ms = round((time.monotonic() - keyword_started) * 1000)
                        stats.append({'keyword': keyword, 'status': query_status,
                                      'collected_count': collected, 'added_count': len(products) - before,
                                      'elapsed_ms': elapsed_ms, 'type_filtered_count': type_filtered,
                                      'duplicate_count': duplicate, 'capacity_skipped_count': capacity_skipped})
                        logger.info('Musinsa keyword_index=%s elapsed_ms=%s collected=%s type_filtered=%s '
                                    'new_candidates=%s total_candidates=%s',
                                    index + 1, elapsed_ms,
                                    collected, type_filtered,
                                    len(products) - before, len(products))
                stage, stage_started = 'context_close', time.monotonic()
                try:
                    context.close()
                except Exception as exc:
                    log_failure(exc)
                    failure_logged = True
                    raise
            finally:
                stage, stage_started = 'browser_close', time.monotonic()
                try:
                    browser.close()
                except Exception as exc:
                    log_failure(exc)
                    failure_logged = True
                    raise
            stage, stage_started = 'playwright_stop', time.monotonic()
    except SearchError as exc:
        if not failure_logged:
            log_failure(exc)
        exc.diagnostics = _search_diagnostics(keywords[:4], stats, len(_ranked_candidates(products, selected)),
                                              round((time.monotonic() - started) * 1000))
        raise
    except Exception as exc:
        if not failure_logged:
            log_failure(exc)
        diagnostics = _search_diagnostics(keywords[:4], stats, len(_ranked_candidates(products, selected)),
                                          round((time.monotonic() - started) * 1000))
        raise SearchError('무신사 Playwright 검색을 완료하지 못했습니다. 브라우저 설치 또는 사이트 상태를 확인하세요.',
                          diagnostics=diagnostics) from exc
    finally:
        logger.info('Musinsa searched_keywords=%s valid_candidates=%s elapsed_ms=%s',
                    len(searched), len(products), round((time.monotonic() - started) * 1000))
    elapsed = round((time.monotonic() - started) * 1000)
    ranked = _ranked_candidates(products, selected)
    if include_diagnostics:
        return ranked, searched, elapsed, _search_diagnostics(keywords[:4], stats, len(ranked), elapsed)
    return ranked, searched, elapsed
