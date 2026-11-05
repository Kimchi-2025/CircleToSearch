import base64
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from main import app
from services.recommendation_service import _thumbnail, cached_catalog_image
from services.tryon_prompt import build_tryon_prompt
from services.tryon_service import TryOnError, _product_image, garment_region, generate_tryon


def jpeg() -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (100, 80), 'navy').save(output, format='JPEG')
    return output.getvalue()


class TryOnTests(unittest.TestCase):
    def test_reuses_full_catalog_image_from_contact_sheet(self):
        url = 'https://image.msscdn.net/tryon-shared-test.jpg'
        picture = jpeg()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {'content-type': 'image/jpeg', 'content-length': str(len(picture))}
        response.iter_bytes.return_value = iter([picture])
        with patch('services.recommendation_service._safe_image_host', return_value=True), \
             patch('services.recommendation_service.httpx.stream', return_value=response) as download:
            self.assertIsNotNone(_thumbnail(url))
        download.assert_called_once()
        self.assertEqual(cached_catalog_image(url), picture)
        with patch('services.tryon_service.httpx.stream') as retry, \
             patch('services.tryon_service._safe_image_host') as validate:
            self.assertEqual(_product_image(url), picture)
        retry.assert_not_called()
        validate.assert_not_called()

    @patch('services.tryon_service.httpx.stream')
    @patch('services.tryon_service._safe_image_host', return_value=True)
    @patch('services.tryon_service.cached_catalog_image', return_value=None)
    def test_cache_miss_reports_remote_http_status(self, cached, host, stream):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 403
        response.headers = {'content-type': 'text/html'}
        stream.return_value = response
        with self.assertRaises(TryOnError) as raised:
            _product_image('https://image.msscdn.net/tryon-missing-test.jpg')
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn('HTTP 403', str(raised.exception))

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_route_sends_two_images_in_one_edit(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        openai_class.return_value.images.edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        product = {'product_id': '123', 'product_name': '네이비 반팔 티셔츠',
                   'brand': '브랜드', 'image_url': 'https://image.msscdn.net/123.jpg'}
        analysis = {'selected_object': {'product_type': '반팔 티셔츠', 'description': '네이비 상의'},
                    'bounding_box': [10, 10, 90, 70]}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key'}, clear=False):
            response = TestClient(app).post('/api/circle-search/try-on',
                data={'product': json.dumps(product), 'analysis': json.dumps(analysis)},
                files={'image': ('person.jpg', picture, 'image/jpeg')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['product_id'], '123')
        self.assertTrue(response.json()['image_data_url'].startswith('data:image/jpeg;base64,'))
        edit = openai_class.return_value.images.edit
        edit.assert_called_once()
        self.assertEqual(len(edit.call_args.kwargs['image']), 2)
        self.assertEqual(edit.call_args.kwargs['n'], 1)
        self.assertEqual(edit.call_args.kwargs['model'], 'gpt-image-2.5-flare')
        self.assertIn('Image 1', edit.call_args.kwargs['prompt'])
        self.assertIn('upper-body garment', edit.call_args.kwargs['prompt'])
        download.assert_called_once_with(product['image_url'])

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_route_accepts_new_person_without_original_box(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        product = {'product_id': '124', 'product_name': '새 상품',
                   'image_url': 'https://image.msscdn.net/124.jpg'}
        analysis = {'selected_object': {'product_type': '상의', 'category': '상의'},
                    'bounding_box': None}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key'}):
            response = TestClient(app).post('/api/circle-search/try-on',
                data={'product': json.dumps(product), 'analysis': json.dumps(analysis)},
                files={'image': ('new-person.jpg', picture, 'image/jpeg')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        edit.assert_called_once()
        prompt = edit.call_args.kwargs['prompt']
        self.assertIn('upper-body garment in image 1', prompt)
        self.assertNotIn('selected area', prompt)

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_pants_and_skirt_use_lower_prompt(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            for kind in ('바지', '스커트'):
                with self.subTest(kind=kind):
                    edit.reset_mock()
                    result = generate_tryon(picture,
                        {'product_id': '1', 'product_name': kind, 'image_url': 'https://image.msscdn.net/1.jpg'},
                        {'product_type': kind, 'description': kind}, [1, 1, 20, 20], 100, 80)
                    self.assertEqual(result['status'], 'success')
                    edit.assert_called_once()
                    prompt = edit.call_args.kwargs['prompt']
                    self.assertIn('lower-body garment', prompt)
                    self.assertIn('existing top', prompt)
                    self.assertNotIn('upper-body garment', prompt)
        self.assertEqual(garment_region('운동화'), 'shoes')
        self.assertIn('upper-body garment', build_tryon_prompt(
            '반팔 티셔츠', None, '티셔츠', '', [1, 1, 20, 20], 100, 80))

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_shoes_use_both_feet_prompt_and_one_edit(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture,
                {'product_id': '3', 'product_name': '화이트 운동화',
                 'image_url': 'https://image.msscdn.net/3.jpg'},
                {'product_type': '운동화', 'description': '화이트 스니커즈'},
                [1, 50, 30, 79], 100, 80)
        self.assertEqual(result['status'], 'success')
        edit.assert_called_once()
        self.assertEqual(len(edit.call_args.kwargs['image']), 2)
        prompt = edit.call_args.kwargs['prompt']
        self.assertIn('BOTH visible feet', prompt)
        self.assertIn('same product', prompt)
        self.assertIn('existing top, pants or skirt', prompt)
        self.assertEqual([garment_region(kind) for kind in ('sneakers', 'boots', 'sandals')],
                         ['shoes', 'shoes', 'shoes'])
        self.assertEqual(garment_region('가방'), 'bag')

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_bag_uses_one_edit_and_rejects_conflicting_vision_category(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        product = {'product_id': '4', 'product_name': '크로스백',
                   'image_url': 'https://image.msscdn.net/4.jpg'}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture, product,
                {'product_type': '크로스백', 'category': '가방', 'description': '검정 가방'},
                [10, 10, 50, 70], 100, 80)
        self.assertEqual(result['status'], 'success')
        edit.assert_called_once()
        prompt = edit.call_args.kwargs['prompt']
        self.assertIn('crossbody bag', prompt)
        self.assertIn('Do not force an unnatural pose', prompt)
        with self.assertRaises(TryOnError) as raised:
            generate_tryon(picture, product,
                {'product_type': '크로스백', 'category': '모자'}, [10, 10, 50, 70], 100, 80)
        self.assertEqual(raised.exception.status_code, 422)
        edit.assert_called_once()

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_bag_name_variants_enable_tryon(self, download, openai_class):
        names = ('Navy Field Bag (XL)', '001 스탠다드 토트_네추럴/네이비',
                 '유니온 토트 [네이비]', '파써블 베이직 토트 미니-네이비',
                 '255BA004U 미드나이트 네이비 컬러 포인트 토트백')
        self.assertEqual([garment_region(name) for name in names], ['bag'] * len(names))
        self.assertEqual([garment_region(name) for name in ('숄더', '크로스', '백팩', '핸드백')],
                         ['bag'] * 4)
        self.assertEqual(garment_region('시계'), 'watch')
        picture = jpeg()
        download.return_value = picture
        openai_class.return_value.images.edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture,
                {'product_id': '7', 'product_name': names[1],
                 'image_url': 'https://image.msscdn.net/7.jpg'},
                {'product_type': '가방', 'category': '가방'}, [10, 10, 50, 70], 100, 80)
        self.assertEqual(result['status'], 'success')
        openai_class.return_value.images.edit.assert_called_once()

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_lower_name_variants_and_category(self, download, openai_class):
        names = ('슬랙스', '데님', '팬츠', '트라우저', 'trouser', '쇼츠')
        self.assertEqual([garment_region(name) for name in names], ['lower'] * len(names))
        self.assertEqual(garment_region('하의'), 'lower')
        picture = jpeg()
        download.return_value = picture
        openai_class.return_value.images.edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture,
                {'product_id': '8', 'product_name': '와이드 트라우저',
                 'image_url': 'https://image.msscdn.net/8.jpg', 'category': '하의'},
                {'product_type': '바지', 'category': '하의'}, [10, 10, 50, 70], 100, 80)
        self.assertEqual(result['status'], 'success')
        openai_class.return_value.images.edit.assert_called_once()

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_opaque_recommendation_names_use_vision_region(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        cases = (('가방', '001 Standard Tote', 'bag'),
                 ('바지', 'Basic 123', 'lower'))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            for index, (vision_type, name, region) in enumerate(cases, start=11):
                with self.subTest(name=name):
                    result = generate_tryon(picture,
                        {'product_id': str(index), 'product_name': name,
                         'category': '모자', 'image_url': f'https://image.msscdn.net/{index}.jpg'},
                        {'product_type': vision_type, 'category': vision_type},
                        [10, 10, 50, 70], 100, 80)
                    self.assertEqual(result['status'], 'success')
                    self.assertIn({'bag': 'new bag', 'lower': 'lower-body garment'}[region],
                                  edit.call_args.kwargs['prompt'])
        self.assertEqual(edit.call_count, 2)

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_watch_tryon_uses_one_edit_and_visible_wrist_prompt(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture,
                {'product_id': '13', 'product_name': 'Limehouse WV244GRGD',
                 'image_url': 'https://image.msscdn.net/13.jpg'},
                {'product_type': '손목시계', 'category': '시계'},
                [10, 10, 50, 70], 100, 80)
        self.assertEqual(result['status'], 'success')
        edit.assert_called_once()
        self.assertEqual(len(edit.call_args.kwargs['image']), 2)
        self.assertIn('visible wrist', edit.call_args.kwargs['prompt'])
        self.assertIn('dial, color, case, band', edit.call_args.kwargs['prompt'])
        self.assertIn('do not invent a wrist', edit.call_args.kwargs['prompt'])

    def test_other_supported_regions_and_selected_category(self):
        for name, region in (('아우터', 'upper'), ('재킷', 'upper'), ('티셔츠', 'upper'),
                             ('운동화', 'shoes'), ('볼캡', 'hat'), ('선글라스', 'glasses'),
                             ('시계', 'watch')):
            with self.subTest(name=name):
                self.assertEqual(garment_region(name), region)
        with self.assertRaises(TryOnError) as raised:
            generate_tryon(jpeg(),
                {'product_id': '9', 'product_name': '와이드 트라우저'},
                {'product_type': '바지', 'category': '모자'}, [10, 10, 50, 70], 100, 80)
        self.assertEqual(raised.exception.status_code, 422)
        with self.assertRaises(TryOnError) as unsupported:
            generate_tryon(jpeg(),
                {'product_id': '10', 'product_name': '와이드 트라우저'},
                {'product_type': '화장품', 'category': '뷰티'}, [10, 10, 50, 70], 100, 80)
        self.assertEqual(unsupported.exception.status_code, 422)

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_hat_and_glasses_classification_and_prompts(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        cases = [('볼캡', '모자', 'head size, angle and perspective'),
                 ('선글라스', '안경', 'eyes, nose and ears')]
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            for index, (kind, category, expected) in enumerate(cases, start=5):
                with self.subTest(kind=kind):
                    result = generate_tryon(picture,
                        {'product_id': str(index), 'product_name': kind,
                         'image_url': f'https://image.msscdn.net/{index}.jpg'},
                        {'product_type': kind, 'category': category}, [10, 10, 50, 70], 100, 80)
                    self.assertEqual(result['status'], 'success')
                    self.assertIn(expected, edit.call_args.kwargs['prompt'])
        self.assertEqual(edit.call_count, 2)
        self.assertEqual(garment_region('beanie'), 'hat')
        self.assertEqual(garment_region('sunglasses'), 'glasses')
        self.assertEqual(garment_region('시계'), 'watch')
        self.assertEqual(garment_region('반지'), 'accessory')

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_belt_with_opaque_recommendation_name(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture,
                {'product_id': '14', 'product_name': 'Classic 041',
                 'image_url': 'https://image.msscdn.net/14.jpg'},
                {'product_type': '허리띠', 'category': '액세서리'},
                [10, 10, 50, 70], 100, 80)
        self.assertEqual(result['status'], 'success')
        edit.assert_called_once()
        self.assertIn('buckle shape', edit.call_args.kwargs['prompt'])
        self.assertIn('belt loops', edit.call_args.kwargs['prompt'])
        self.assertEqual(garment_region('벨트'), 'belt')

    @patch('services.tryon_service.OpenAI')
    @patch('services.tryon_service._product_image')
    def test_generic_accessory_and_unclear_type(self, download, openai_class):
        picture = jpeg()
        download.return_value = picture
        edit = openai_class.return_value.images.edit
        edit.return_value = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture).decode())], usage=None)
        product = {'product_id': '15', 'product_name': 'Style 001',
                   'image_url': 'https://image.msscdn.net/15.jpg'}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'mock-key',
                                     'OPENAI_TRYON_MODEL': 'gpt-image-2.5-flare'}):
            result = generate_tryon(picture, product,
                {'product_type': '반지', 'category': '주얼리', 'description': '금색 링'},
                None, 100, 80)
        self.assertEqual(result['status'], 'success')
        edit.assert_called_once()
        self.assertIn('fashion accessory', edit.call_args.kwargs['prompt'])
        self.assertIn('anatomically appropriate visible location', edit.call_args.kwargs['prompt'])
        with self.assertRaises(TryOnError) as unclear:
            generate_tryon(picture, product,
                {'product_type': '액세서리', 'category': '패션잡화'}, None, 100, 80)
        self.assertEqual(unclear.exception.status_code, 422)
        edit.assert_called_once()

    @patch('services.tryon_service.httpx.stream')
    @patch('services.tryon_service._safe_image_host', return_value=False)
    def test_untrusted_image_url_is_rejected(self, host, stream):
        with self.assertRaises(TryOnError) as raised:
            _product_image('http://127.0.0.1/private.png')
        self.assertEqual(raised.exception.status_code, 422)
        stream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
