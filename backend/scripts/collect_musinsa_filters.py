"""Collect public Musinsa search metadata without changing application services."""

import argparse
import json
import os
import re
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


BASE = "https://www.musinsa.com"
MENU_URL = f"{BASE}/menu/category"
MENU_API_PATH = "/api2/dp/v5/menu?tabId=category"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "musinsa_search_metadata.json"
CATEGORIES_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "musinsa_categories.json"
FASHION_REFERENCE = Path(__file__).resolve().parents[1] / "data" / "musinsa_fashion_reference.json"
FASHION_TERMS_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "musinsa_fashion_search_terms.json"
FASHION_PROMPT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "musinsa_fashion_vision_prompt_v2.txt"
SAMPLES = (
    ("티셔츠", ("반소매 티셔츠", "티셔츠"), "상의"),
    ("청바지", ("데님 팬츠", "청바지", "데님"), "바지"),
    ("자켓", ("자켓", "재킷"), "아우터"),
)
FASHION_SEARCHES = (
    ("티셔츠", "상의"), ("청바지", "바지"), ("자켓", "아우터"),
    ("셔츠", "상의"), ("니트", "상의"), ("맨투맨", "상의"),
    ("코트", "아우터"), ("패딩", "아우터"),
    ("슬랙스", "바지"), ("반바지", "바지"), ("스커트", "원피스/스커트"),
    ("운동화", "신발"), ("구두", "신발"), ("가방", "가방"),
    ("모자", "모자"), ("시계", "소품"), ("벨트", "소품"),
)
FASHION_TYPE_MARKERS = {
    "상의": ("티셔츠", "셔츠", "니트", "스웨터", "맨투맨", "스웨트", "후드", "블라우스", "럭비", "탑"),
    "아우터": ("자켓", "재킷", "코트", "패딩", "점퍼", "블레이저", "블루종", "무스탕", "아우터",
             "바람막이", "트러커", "야상", "플리스", "후리스", "카디건", "가디건", "베스트"),
    "바지": ("바지", "팬츠", "데님", "슬랙스", "조거", "트라우저", "레깅스"),
    "원피스/스커트": ("스커트", "치마", "원피스"),
    "신발": ("운동화", "스니커즈", "구두", "로퍼", "부츠", "샌들", "슬리퍼", "신발", "워커", "플랫", "힐", "슈즈"),
    "가방": ("가방", "백팩", "토트", "숄더", "크로스", "파우치", "더플", "핸드백",
             "미니백", "에코백", "보스턴백", "메신저백"),
    "모자": ("모자", "캡", "비니", "버킷", "햇", "베레모"),
    "소품": ("시계", "워치", "벨트", "버클"),
}
UNHELPFUL_PREFIXES = ("남자 ", "여자 ", "남성 ", "여성 ", "여름 ", "겨울 ")
GENERIC_TERM_PARTS = tuple({part for markers in FASHION_TYPE_MARKERS.values() for part in markers} | {
    "긴팔", "긴소매", "반팔", "반소매", "레이어드", "스트라이프", "크롭", "7부", "럭비", "체크",
    "브이넥", "헨리넥", "집업", "무지", "오버핏", "기모", "반집업", "후드티", "긴팔티",
    "스웨이드", "워크", "레더", "가죽", "필드", "데님", "퍼", "항공", "숏", "하프", "롱",
    "발마칸", "트렌치", "떡볶이", "카코트", "하이넥", "캐시미어", "경량", "조끼",
    "와이드", "세미", "흑청", "진청", "연청", "밴딩", "부츠컷", "히든", "트레이닝",
    "운동", "청", "반바지", "미니", "미디", "플리츠", "새틴", "메리제인", "키높이", "통굽",
    "정장", "슬링백", "더비", "보부상", "노트북", "책", "등산", "러닝", "메탈", "손목",
    "위빙", "웨스턴", "에코", "숄더", "크로스", "토트", "캡", "볼",
})
BLOCKED_TEXT = ("접속이 일시적으로 제한", "비정상적인 접근", "access denied", "captcha")
META_ONLY_FILTERS = {"brand", "brandLineUp", "priceRange"}


class CollectionError(Exception):
    def __init__(self, message, blocked=False):
        super().__init__(message)
        self.blocked = blocked


def category_url(code):
    return f"{BASE}/category/{code}/goods?gf=A"


def category_nodes(items, parents=()):
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code = item.get("value") or item.get("categoryCode") or item.get("code")
        name = (item.get("displayText") or item.get("categoryName") or
                item.get("categoryTitle") or item.get("name"))
        if not isinstance(code, str) or not re.fullmatch(r"\d{3,15}", code) or not name:
            continue
        parent = item.get("parentCategoryCode") or (parents[-1][0] if parents else None)
        path = parents + ((code, name),)
        yield {"id": code, "name": name, "parent_id": parent,
               "url": None, "path": [label for _, label in path]}
        yield from category_nodes(item.get("categoryList") or item.get("children") or
                                  item.get("subCategories"), path)


def menu_category_nodes(items, parents=()):
    """Flatten the public category menu, including its non-category grouping rows."""
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code, name, url = item.get("code"), item.get("title"), item.get("linkUrl")
        path = parents
        if (isinstance(code, str) and re.fullmatch(r"\d{3,15}", code) and name and
                isinstance(url, str) and url.startswith(f"{BASE}/category/{code}")):
            path = parents + ((code, name),)
            yield {"id": code, "name": name,
                   "parent_id": parents[-1][0] if parents else None,
                   "url": url, "path": [label for _, label in path]}
        yield from menu_category_nodes(item.get("list"), path)


def read_menu(page):
    with page.expect_response(
        lambda response: MENU_API_PATH in response.url and response.status == 200,
        timeout=15000,
    ) as menu_response:
        response = page.goto(MENU_URL, wait_until="domcontentloaded", timeout=20000)
        if not response or response.status != 200:
            raise CollectionError(f"Menu HTTP {response.status if response else None}")
    payload = menu_response.value.json()
    roots = payload.get("data", {}).get("list")
    if not isinstance(roots, list) or not roots:
        raise CollectionError("No structured category menu was returned")
    rows = list(menu_category_nodes(roots))
    if not rows:
        raise CollectionError("Category menu contained no verified category IDs")
    return page.url, roots, rows


def filter_options(items, parent_value=None):
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name, value = item.get("displayText"), item.get("value")
        key = item.get("parameterKey")
        if name and value is not None:
            identity = (key, str(value), parent_value)
            if identity not in seen:
                seen.add(identity)
                yield {"name": name, "value": value, "parameter_key": key,
                       "parent_value": parent_value}
        yield from filter_options(item.get("list"), value)


def read_page(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
    status = response.status if response else None
    if status in (403, 429):
        raise CollectionError(f"HTTP {status}", blocked=True)
    if status != 200:
        raise CollectionError(f"HTTP {status}")
    body_start = page.locator("body").inner_text(timeout=3000)[:2000].lower()
    if any(marker in body_start for marker in BLOCKED_TEXT):
        raise CollectionError("접근 제한 또는 CAPTCHA 감지", blocked=True)
    script = page.locator("script#__NEXT_DATA__")
    if not script.count():
        raise CollectionError("구조화된 검색 데이터가 없습니다")
    try:
        props = json.loads(script.text_content(timeout=3000))["props"]["pageProps"]
        queries = props["dehydratedState"]["queries"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CollectionError("검색 데이터 JSON 구조를 읽지 못했습니다") from exc
    details, related, category_items = None, [], []
    for query in queries:
        data = query.get("state", {}).get("data")
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("detail"), dict):
            details = payload["detail"]
            category_items.extend(details.get("category", {}).get("list") or [])
        if (query.get("queryKey") or [None])[0] == "category/Category":
            category_items.extend(payload.get("list") or [])
        if isinstance(payload.get("relatedKeywords"), list):
            related = [term.strip() for term in payload["relatedKeywords"]
                       if isinstance(term, str) and term.strip()]
    return page.url, details, related, category_items


def save_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collect_categories_only(output_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in
                          ("image", "font", "media") else route.continue_())
            try:
                page = context.new_page()
                source_url, _, rows = read_menu(page)
            finally:
                context.close()
        finally:
            browser.close()

    menu_depth = max(len(row["path"]) for row in rows)
    unique = {row["id"]: row for row in rows if len(row["path"]) <= 2}
    categories = [{"id": row["id"], "name": row["name"],
                   "parent_id": row["parent_id"], "depth": len(row["path"])}
                  for row in unique.values()]
    major_names = list(dict.fromkeys(row["name"] for row in categories if row["depth"] == 1))
    middle_names = list(dict.fromkeys(row["name"] for row in categories if row["depth"] == 2))
    vision_text = ("무신사 대분류: " + ", ".join(major_names) + "\n"
                   "무신사 중분류: " + ", ".join(middle_names))
    result = {"source": "musinsa", "source_url": source_url,
              "collected_at": datetime.now(timezone.utc).isoformat(),
              "menu_max_depth": menu_depth, "categories": categories,
              "vision_category_names": {"major": major_names, "middle": middle_names},
              "vision_prompt_text": vision_text}
    save_atomic(output_path, result)
    print(f"saved {output_path} major={len(major_names)} middle={len(middle_names)} "
          f"menu_depth={menu_depth} categories={len(categories)}")
    return result


def useful_fashion_term(term, major):
    if not isinstance(term, str) or not term or len(term) > 30 or "\n" in term:
        return False
    if term.startswith(UNHELPFUL_PREFIXES):
        return False
    compact = "".join(term.lower().split())
    if not any(marker in compact for marker in FASHION_TYPE_MARKERS[major]):
        return False
    if major == "상의" and any(other in compact for other in ("패딩", "코트", "자켓", "재킷", "청바지")):
        return False
    reachable = {0}
    for index in range(len(compact)):
        if index in reachable:
            reachable.update(index + len(part) for part in GENERIC_TERM_PARTS
                             if compact.startswith(part, index))
    return len(compact) in reachable


def save_text_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collect_fashion_terms(output_path, prompt_path):
    collected_at = datetime.now(timezone.utc).isoformat()
    reference = json.loads(FASHION_REFERENCE.read_text(encoding="utf-8"))
    official = [{"major": group["major"], "term": item["name"],
                 "category_id": item["id"], "source_type": "official_category"}
                for group in reference["categories"] for item in group["middle_categories"]]
    pages, accepted = [], {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in
                          ("image", "font", "media") else route.continue_())
            try:
                page = context.new_page()
                for index, (keyword, major) in enumerate(FASHION_SEARCHES, 1):
                    url = f"{BASE}/search/goods?" + urlencode({"keyword": keyword})
                    visited_at = datetime.now(timezone.utc).isoformat()
                    try:
                        actual_url, details, terms, _ = read_page(page, url)
                        if details is None:
                            raise CollectionError("Search filter data was missing")
                        selected = list(dict.fromkeys(term for term in terms
                                                      if useful_fashion_term(term, major)))
                        pages.append({"search_keyword": keyword, "major": major,
                                      "source_url": actual_url, "collected_at": visited_at,
                                      "status": "ok", "raw_related_count": len(terms),
                                      "accepted_count": len(selected), "raw_terms": terms,
                                      "accepted_terms": selected})
                        for term in selected:
                            row = accepted.setdefault(term, {"term": term, "source_type": "related_search",
                                                             "majors": [], "search_keywords": [],
                                                             "source_urls": [], "collected_at": visited_at})
                            if major not in row["majors"]:
                                row["majors"].append(major)
                            if keyword not in row["search_keywords"]:
                                row["search_keywords"].append(keyword)
                            if actual_url not in row["source_urls"]:
                                row["source_urls"].append(actual_url)
                        print(f"search {index}/{len(FASHION_SEARCHES)}: raw={len(terms)} accepted={len(selected)}")
                    except (CollectionError, PlaywrightError, OSError) as exc:
                        blocked = isinstance(exc, CollectionError) and exc.blocked
                        pages.append({"search_keyword": keyword, "major": major,
                                      "source_url": url, "collected_at": visited_at,
                                      "status": "blocked" if blocked else "failed",
                                      "error": str(exc)[:300], "raw_related_count": 0,
                                      "accepted_count": 0, "raw_terms": [], "accepted_terms": []})
                        print(f"search {index}/{len(FASHION_SEARCHES)} failed: {str(exc)[:160]}")
                        if index <= 3 or blocked:
                            break
                    if index == 3:
                        if any(item["status"] != "ok" or not item["raw_related_count"]
                               for item in pages):
                            print("pilot incomplete: stopping before remaining search pages")
                            break
                        print("pilot complete: 3 search pages; structured related terms confirmed")
            finally:
                context.close()
        finally:
            browser.close()

    result = {"source": "musinsa", "collected_at": collected_at,
              "reference_file": FASHION_REFERENCE.name,
              "visited_search_pages": len(pages), "search_pages": pages,
              "official_categories": official,
              "related_search_terms": list(accepted.values())}
    save_atomic(output_path, result)

    lines = ["무신사 패션 상품 검색 참고 용어 (공식 카테고리 + 실제 연관 검색어)"]
    for group in reference["categories"]:
        major = group["major"]
        names = list(dict.fromkeys(group["vision_terms"] +
                                   [term for term, row in accepted.items() if major in row["majors"]]))
        lines.append(f"{major}: {', '.join(names)}")
    lines.extend(["", "참고 지침: 이미지에서 선택한 상품 유형을 우선 판단하고 위 명칭을 참고하되 강제로 선택하지 마세요. "
                  "검색어는 짧고 자연스럽게, 상품 유형 중심으로 색상 또는 가장 중요한 특징만 선택적으로 붙이세요. "
                  "핵심 1개, 대체 2개, 광범위 1개를 생성하며 세부 로고 문자와 숫자, 특징 나열은 피하세요."])
    save_text_atomic(prompt_path, "\n".join(lines) + "\n")
    print(f"saved {output_path} and {prompt_path}; valid_related_terms={len(accepted)}")
    return result


def build_tree(categories):
    records = {code: {**row, "children": []} for code, row in categories.items()}
    roots = []
    for code, row in sorted(records.items()):
        parent = row["parent_id"]
        if parent in records and parent != code:
            records[parent]["children"].append(row)
        else:
            roots.append(row)
    return roots


def choose_sample(categories, keyword, aliases, root_name, source_url):
    matches = []
    for row in categories.values():
        if (source_url in row["source_urls"] and
                any(alias.replace(" ", "") in row["name"].replace(" ", "") for alias in aliases)):
            matches.append(row)
    matches.sort(key=lambda row: (row["path"][0] != root_name,
                                  next((i for i, alias in enumerate(aliases)
                                        if alias.replace(" ", "") in row["name"].replace(" ", "")),
                                       len(aliases)), len(row["path"]),
                                  len(row["name"]), row["id"]))
    return matches[0] if matches else None


def collect(mode, output_path, discover_only=False):
    categories, related, filters, statuses = {}, {}, {}, []
    collected_at = datetime.now(timezone.utc).isoformat()
    successful_pages = 0
    discovery_pages = 0

    def snapshot():
        return {"source": "musinsa", "collected_at": collected_at,
                "category_tree": build_tree(categories),
                "related_search_terms": list(related.values()),
                "filters": list(filters.values()), "collection_status": statuses}

    def add_categories(rows, source_url, kind=None, category_id=None):
        added = 0
        for row in rows:
            code = row["id"]
            if code not in categories:
                categories[code] = {**row, "source_urls": [source_url]}
                added += 1
            else:
                existing = categories[code]
                if not existing["parent_id"] and row["parent_id"]:
                    existing["parent_id"] = row["parent_id"]
                    existing["path"] = row["path"]
                if (kind == "search" or code == category_id) and source_url not in existing["source_urls"]:
                    existing["source_urls"].append(source_url)
        return added

    def visit(page, url, kind, keyword=None, category_id=None):
        nonlocal successful_pages
        try:
            actual_url, details, terms, category_items = read_page(page, url)
            if details is None:
                raise CollectionError("필터 구조화 데이터가 없습니다")
            new_categories = add_categories(category_nodes(category_items), actual_url, kind, category_id)
            if category_id in categories:
                categories[category_id]["url"] = actual_url
            for term in terms:
                related[(keyword or category_id, term)] = {
                    "search_keyword": keyword, "category_id": category_id,
                    "term": term, "source_url": actual_url,
                    "source_path": "__NEXT_DATA__.dehydratedState.relationKeyword"}
            new_filters = 0
            for key, group in details.items():
                if key == "category" or not isinstance(group, dict):
                    continue
                options = list(filter_options(group.get("list")))
                if not options and not group.get("apiUrl"):
                    continue
                scope = category_id or keyword
                filters[(kind, scope, key)] = {
                    "category_id": category_id, "search_keyword": keyword,
                    "key": key, "title": group.get("title"),
                    "parameter_key": next((o["parameter_key"] for o in options if o["parameter_key"]), None),
                    "is_multiple": group.get("isMultiple"), "api_url": group.get("apiUrl") or None,
                    "option_count": len(options),
                    "options": [] if key in META_ONLY_FILTERS else options,
                    "source_url": actual_url,
                    "source_path": f"__NEXT_DATA__.dehydratedState.Filters.detail.{key}"}
                new_filters += 1
            statuses.append({"kind": kind, "search_keyword": keyword, "category_id": category_id,
                             "url": actual_url, "status": "ok", "new_categories": new_categories,
                             "related_terms": len(terms), "filter_groups": new_filters})
            successful_pages += 1
            print(f"{kind}: {keyword or category_id} categories+{new_categories} related={len(terms)} filters={new_filters}")
            return True
        except (CollectionError, PlaywrightError, OSError) as exc:
            blocked = isinstance(exc, CollectionError) and exc.blocked
            statuses.append({"kind": kind, "search_keyword": keyword, "category_id": category_id,
                             "url": url, "status": "blocked" if blocked else "failed",
                             "error": str(exc)[:300]})
            print(f"{kind}: {keyword or category_id} failed: {str(exc)[:160]}")
            return not blocked
        finally:
            if not discover_only and (successful_pages or not output_path.exists()):
                save_atomic(output_path, snapshot())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in
                          ("image", "font", "media") else route.continue_())
            page = context.new_page()
            try:
                roots = []
                if mode == "all":
                    discovery_pages += 1
                    try:
                        menu_url, menu_roots, menu_rows = read_menu(page)
                        roots = [row["id"] for row in menu_category_nodes(menu_roots)
                                 if row["parent_id"] is None]
                        added = add_categories(menu_rows, menu_url)
                        statuses.append({"kind": "menu", "url": menu_url, "status": "ok",
                                         "new_categories": added})
                        print(f"menu: roots={len(roots)} categories={len(categories)}")
                    except (CollectionError, PlaywrightError, ValueError, KeyError, TypeError) as exc:
                        statuses.append({"kind": "menu", "url": MENU_URL, "status": "failed",
                                         "error": str(exc)[:300]})
                        print(f"menu: unavailable ({str(exc)[:160]}); using search discovery")
                    for code in dict.fromkeys(roots):
                        discovery_pages += 1
                        try:
                            actual_url, _, _, items = read_page(page, category_url(code))
                            added = add_categories(category_nodes(items), actual_url)
                            print(f"root: {code} categories+{added}")
                        except (CollectionError, PlaywrightError, OSError) as exc:
                            statuses.append({"kind": "discovery", "category_id": code,
                                             "url": category_url(code), "status": "failed",
                                             "error": str(exc)[:300]})
                            print(f"root: {code} discovery failed: {str(exc)[:160]}")
                sample_targets = []
                for keyword, aliases, root_name in SAMPLES:
                    if mode == "all":
                        discovery_pages += 1
                    url = f"{BASE}/search/goods?" + urlencode({"keyword": keyword})
                    if not visit(page, url, "search", keyword=keyword):
                        return snapshot()
                    if mode == "sample":
                        target = choose_sample(categories, keyword, aliases, root_name, page.url)
                        if target:
                            sample_targets.append(target["id"])
                        else:
                            statuses.append({"kind": "category", "search_keyword": keyword,
                                             "status": "not_found", "error": "일치하는 공식 카테고리 없음"})
                if mode == "all":
                    print(f"discovered categories={len(categories)}; expected page visits="
                          f"{discovery_pages + len(categories)} "
                          f"({discovery_pages} discovery + {len(categories)} category; may grow)")
                if discover_only:
                    return snapshot()
                queue = deque(dict.fromkeys(sample_targets if mode == "sample" else categories))
                visited = set()
                while queue:
                    code = queue.popleft()
                    if code in visited:
                        continue
                    visited.add(code)
                    if not visit(page, category_url(code), "category", category_id=code):
                        break
                    if mode == "all":
                        queue.extend(code for code in categories if code not in visited)
            finally:
                context.close()
        finally:
            browser.close()
    if not discover_only and (successful_pages or not output_path.exists()):
        save_atomic(output_path, snapshot())
    return snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", action="store_true", help="티셔츠·청바지·자켓 검색과 대표 카테고리 3개")
    mode.add_argument("--all", action="store_true", help="검색 페이지에서 발견한 전체 카테고리를 순차 탐색")
    mode.add_argument("--categories-only", action="store_true",
                      help="공개 메뉴의 대분류·중분류만 별도 JSON으로 저장")
    mode.add_argument("--fashion-terms", action="store_true",
                      help="패션 검색어 17개의 공개 연관 검색어와 Vision 참고 사전 v2 수집")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--discover-only", action="store_true",
                        help="Discover categories without visiting every category or writing JSON (--all only)")
    args = parser.parse_args()
    if args.discover_only and not args.all:
        parser.error("--discover-only requires --all")
    output_path = (args.output or (CATEGORIES_OUTPUT if args.categories_only else DEFAULT_OUTPUT)).resolve()
    if args.categories_only:
        collect_categories_only(output_path)
        return
    if args.fashion_terms:
        collect_fashion_terms((args.output or FASHION_TERMS_OUTPUT).resolve(), FASHION_PROMPT_OUTPUT)
        return
    result = collect("sample" if args.sample else "all", output_path, args.discover_only)
    print("discovered categories" if args.discover_only else f"saved {output_path} categories",
          sum(1 for _ in walk_tree(result["category_tree"])),
          "related", len(result["related_search_terms"]), "filters", len(result["filters"]))


def walk_tree(nodes):
    for node in nodes:
        yield node
        yield from walk_tree(node["children"])


if __name__ == "__main__":
    main()
