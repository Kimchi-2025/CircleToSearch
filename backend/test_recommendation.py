import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from services.musinsa_service import extract_products, search_products
from services.recommendation_service import (
    ComparedCandidate, ComparisonOutput, _comparison_candidates, _contact_sheet,
    compare_candidates, recommend,
)


class RecommendationTests(unittest.TestCase):
    def test_comparison_preselection_keeps_relevance_and_query_diversity(self):
        products = [dict(product_id=str(index), product_name='네이비 레터링 반팔티',
                         matched_search_keyword='첫 검색어') for index in range(1, 19)]
        products += [dict(product_id=str(index), product_name='네이비 그래픽 반팔티',
                          matched_search_keyword='둘째 검색어') for index in range(19, 26)]
        products += [dict(product_id=str(index), product_name='검정 무지 반팔티',
                          matched_search_keyword='첫 검색어') for index in range(26, 31)]
        selected = {'dominant_color': '네이비', 'pattern': '레터링'}
        chosen = _comparison_candidates(products, selected)
        self.assertEqual(len(chosen), 20)
        self.assertEqual(len({item['product_id'] for item in chosen}), 20)
        self.assertEqual(sum(item['matched_search_keyword'] == '둘째 검색어' for item in chosen), 7)
        self.assertTrue(all('검정 무지' not in item['product_name'] for item in chosen))
        self.assertEqual(len(_comparison_candidates(products, {})), 30)

    @patch('services.recommendation_service._thumbnail')
    def test_contact_sheet_downloads_shared_url_once(self, thumbnail):
        thumbnail.return_value = Image.new('RGB', (185, 185), 'navy')
        products = [dict(product_id=str(index), image_url=f'https://image.msscdn.net/{url}.jpg')
                    for index, url in ((1, 'same'), (2, 'same'), (3, 'other'))]
        timings = {}
        sheet, usable = _contact_sheet(products, timings)
        self.assertTrue(sheet)
        self.assertEqual([item['product_id'] for item in usable], ['1', '2', '3'])
        self.assertEqual(thumbnail.call_count, 2)
        self.assertIn('image_download_elapsed_ms', timings)
        self.assertIn('contact_sheet_build_elapsed_ms', timings)

    def _search_with_pages(self, markups, product_type, include_diagnostics=False):
        page = MagicMock()
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = ''
        page.content.side_effect = markups
        context = MagicMock()
        context.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        with patch('services.musinsa_service.sync_playwright', return_value=manager):
            result = search_products(['첫 검색어', '두 번째', '세 번째', '네 번째'],
                                     product_type, include_diagnostics=include_diagnostics)
        return result, page

    def _cards(self, ids, name):
        return ''.join(f'''<div class="UIItemContainerStyle-item">
          <a class="gtm-view-item-list" data-item-id="{pid}" data-item-list-id="goods_list"
             href="/products/{pid}"><img src="https://image.msscdn.net/{pid}.jpg"></a>
          <a class="gtm-select-item" data-item-id="{pid}" aria-label="상품상세"
             href="/products/{pid}">{name}</a></div>''' for pid in ids)

    def test_search_stops_after_20_matching_type_candidates(self):
        first = (self._cards(range(1, 9), '네이비 레터링 반팔티') +
                 self._cards(range(9, 17), '검정 그래픽 반팔티') +
                 self._cards(range(17, 23), '흰색 무지 반팔티') +
                 self._cards(range(100, 105), '청바지'))
        (products, searched, _), page = self._search_with_pages([first], '반팔 티셔츠')
        self.assertEqual((len(products), len(searched), page.goto.call_count), (22, 1, 1))
        self.assertTrue(all('반팔티' in item['product_name'] for item in products))

    def test_search_stops_after_second_query_with_10_to_19_unique_candidates(self):
        first = self._cards(range(1, 13), '네이비 그래픽 반팔티')
        second = self._cards(range(10, 18), '레터링 반팔티')
        (products, searched, _), page = self._search_with_pages([first, second], '반팔 티셔츠')
        self.assertEqual((len(products), len(searched), page.goto.call_count), (17, 2, 2))
        self.assertEqual(len({item['product_id'] for item in products}), 17)

    def test_low_quality_first_query_adds_second_and_reports_actual_counts(self):
        first = self._cards(range(1, 31), '블랙 무지 반팔티')
        second = self._cards(range(31, 46), '네이비 레터링 반팔티')
        selected = {'product_type': '반팔 티셔츠', 'dominant_color': '네이비', 'pattern': '레터링'}
        (products, searched, _, diagnostics), page = self._search_with_pages(
            [first, second], selected, include_diagnostics=True)
        self.assertEqual((len(products), len(searched), page.goto.call_count), (30, 2, 2))
        self.assertEqual([item['status'] for item in diagnostics['query_stats']],
                         ['executed', 'executed', 'skipped', 'skipped'])
        self.assertEqual([(item['collected_count'], item['added_count'])
                          for item in diagnostics['query_stats'][:2]], [(30, 30), (15, 15)])
        self.assertTrue(all('네이비' in item['product_name'] for item in products[:15]))

    def test_third_query_only_candidates_triggers_fourth_for_diversity(self):
        third = self._cards(range(1, 26), '오버핏 검정 그래픽 반팔티')
        fourth = self._cards(range(26, 41), '네이비 레터링 반팔티')
        selected = {'product_type': '반팔 티셔츠', 'dominant_color': '네이비', 'pattern': '레터링'}
        (products, searched, _, diagnostics), page = self._search_with_pages(
            ['', '', third, fourth], selected, include_diagnostics=True)
        self.assertEqual((len(products), len(searched), page.goto.call_count), (30, 4, 4))
        self.assertEqual(diagnostics['executed_query_count'], 4)
        self.assertGreaterEqual(sum(item['matched_search_keyword'] == '네 번째' for item in products), 10)

    def test_existing_musinsa_card_selectors(self):
        markup = '''<div class="UIItemContainerStyle-item">
          <a class="gtm-view-item-list" data-item-id="123" data-item-list-id="goods_list"
             href="/products/123"><img src="https://image.msscdn.net/a.jpg"></a>
          <a class="gtm-select-item" data-item-id="123" aria-label="상품상세" href="/products/123">검정 재킷</a>
          <a href="/brand/sample">브랜드</a>
          <span class="GoodsItem-price__Gt-sc-x">59,000원</span>
        </div>'''
        products = extract_products(markup + markup, '검정 재킷', 30)
        self.assertEqual(len(products), 1)
        self.assertEqual((products[0]['product_id'], products[0]['price']), ('123', 59000))
        self.assertEqual(products[0]['matched_search_keyword'], '검정 재킷')

    @patch('services.recommendation_service.compare_candidates')
    @patch('services.recommendation_service.search_products', return_value=(
        [], ['검정 재킷'], 10, {'executed_query_count': 1, 'candidate_count': 0,
                             'query_stats': [], 'search_elapsed_ms': 10}))
    def test_no_candidates_skips_openai_and_caches(self, search, compare):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            first = recommend(b'unique-test-crop', {'description': '검정 재킷'}, ['검정 재킷'])
            second = recommend(b'unique-test-crop', {'description': '검정 재킷'}, ['검정 재킷'])
        self.assertEqual(first['search_status'], 'no_candidates')
        self.assertTrue(second['cached'])
        search.assert_called_once()
        compare.assert_not_called()

    @patch('services.recommendation_service._contact_sheet')
    @patch('services.recommendation_service.OpenAI')
    def test_one_comparison_call_rejects_unknown_and_duplicate_ids(self, openai_class, sheet):
        product = {'product_id': '123', 'product_name': '검정 재킷', 'brand': '브랜드',
                   'product_url': 'https://www.musinsa.com/products/123', 'image_url': None,
                   'price': None, 'matched_search_keyword': '검정 재킷'}
        sheet.return_value = (b'sheet', [product])
        parsed = ComparisonOutput(recommendations=[
            ComparedCandidate(product_id='999', similarity_grade='similar'),
            ComparedCandidate(product_id='123', similarity_grade='similar'),
            ComparedCandidate(product_id='123', similarity_grade='somewhat_similar'),
            ComparedCandidate(product_id='123', similarity_grade='not_similar'),
        ])
        openai_class.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed, model='test-model',
            usage=SimpleNamespace(input_tokens=100, output_tokens=42, total_tokens=142))
        image = Image.new('RGB', (8, 8), 'black')
        data = io.BytesIO()
        image.save(data, format='JPEG')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            picked, _, _, diagnostics = compare_candidates(
                data.getvalue(), {'description': '검정 재킷'}, [product], 'test-model', include_diagnostics=True)
        self.assertEqual([item['product_id'] for item in picked], ['123'])
        self.assertEqual(picked[0]['rank'], 1)
        self.assertEqual(picked[0]['similarity_grade'], 'similar')
        self.assertNotIn('similarity_reason', ComparedCandidate.model_json_schema()['properties'])
        self.assertNotIn('similarity_reason', picked[0])
        self.assertEqual(diagnostics['excluded_reasons']['not_similar'], 1)
        self.assertEqual(diagnostics['input_image_count'], 2)
        self.assertEqual(diagnostics['crop_width_px'], 8)
        self.assertEqual(diagnostics['contact_sheet_image_bytes'], len(b'sheet'))
        self.assertEqual(diagnostics['input_image_bytes'],
                         diagnostics['crop_image_bytes'] + diagnostics['contact_sheet_image_bytes'])
        self.assertEqual(diagnostics['output_tokens'], 42)
        openai_class.return_value.responses.parse.assert_called_once()
        prompt = openai_class.return_value.responses.parse.call_args.kwargs['input'][0]['content'][0]['text']
        self.assertIn('product type as the first requirement', prompt)
        self.assertIn('lettering/logo', prompt)

    @patch('services.recommendation_service.search_products')
    @patch('services.recommendation_service._contact_sheet')
    @patch('services.recommendation_service.OpenAI')
    def test_response_distinguishes_ai_choices_from_server_exclusions(self, openai_class, sheet, search):
        product = {'product_id': '123', 'product_name': '네이비 레터링 반팔티', 'brand': '브랜드',
                   'product_url': 'https://www.musinsa.com/products/123', 'image_url': None,
                   'price': None, 'matched_search_keyword': '네이비 반팔티'}
        search.return_value = ([product], ['네이비 반팔티'], 8,
            {'executed_query_count': 1, 'candidate_count': 1,
             'query_stats': [{'keyword': '네이비 반팔티', 'status': 'executed',
                              'collected_count': 2, 'added_count': 1, 'elapsed_ms': 8}],
             'search_elapsed_ms': 8})
        sheet.return_value = (b'sheet', [product])
        parsed = ComparisonOutput(recommendations=[
            ComparedCandidate(product_id='123', similarity_grade='similar'),
            ComparedCandidate(product_id='999', similarity_grade='similar'),
            ComparedCandidate(product_id='123', similarity_grade='somewhat_similar'),
        ])
        openai_class.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed, model='mock', usage=None)
        image = Image.new('RGB', (8, 8), 'navy')
        data = io.BytesIO()
        image.save(data, format='JPEG')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            result = recommend(data.getvalue(), {'description': '네이비 반팔티',
                'product_type': '반팔 티셔츠'}, ['네이비 반팔티'])
        comparison = result['search_diagnostics']['vision_comparison']
        self.assertEqual((comparison['input_candidate_count'], comparison['selected_count'],
                          comparison['final_count'], comparison['excluded_count']), (1, 3, 1, 2))
        self.assertEqual(comparison['excluded_reasons']['unknown_id'], 1)
        self.assertEqual(comparison['excluded_reasons']['duplicate_id'], 1)
        self.assertEqual(result['recommended_count'], 1)
        openai_class.return_value.responses.parse.assert_called_once()


if __name__ == '__main__':
    unittest.main()
