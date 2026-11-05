import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services import vision_service as vision
from test_api import image_bytes


def output(polygon=None, needs_review=False, status="success"):
    return vision.VisionOutput(
        status=status,
        selected_object=(vision.SelectedObject(
            category="shoe", product_type="sneaker", description="White sneaker",
            dominant_color="white", secondary_colors=[], pattern=None,
            visible_text_or_logo=None, material_appearance=None,
            fit_or_shape="low top", style="casual") if status == "success" else None),
        geometry=(vision.Geometry(bounding_box=[20, 10, 60, 50], polygon=polygon,
                                  needs_review=needs_review) if status == "success" else None),
        search_keywords=(vision.SearchKeywords(
            primary="흰 운동화", alternatives=["흰 스니커즈", "캐주얼 운동화"], broad="운동화")
            if status == "success" else None),
    )


class VisionTests(unittest.TestCase):
    def test_missing_keywords_are_recovered_in_same_mock_vision_call(self):
        missing = output([[20, 10], [60, 10], [60, 50], [20, 50]])
        missing.search_keywords = None
        missing.selected_object.product_type = '반팔 티셔츠'
        missing.selected_object.dominant_color = '네이비'
        missing.selected_object.pattern = '레터링 프린트'
        missing.selected_object.fit_or_shape = '오버핏'
        missing.selected_object.style = '캐주얼'
        fake_response = SimpleNamespace(output_parsed=missing, model='gpt-6-luna', usage=None)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}), patch.object(vision, 'OpenAI') as client:
            client.return_value.responses.parse.return_value = fake_response
            result = vision._call_vision(image_bytes(), 100, 80, [40, 30],
                                         [[20, 10], [60, 10], [60, 50], [20, 50]], 'gpt-6-luna')
        self.assertEqual(result['status'], 'success')
        words = result['search_keywords']
        self.assertEqual(len({words['primary'], *words['alternatives'], words['broad']}), 4)
        self.assertEqual(words['primary'], '네이비 레터링 반팔 티셔츠')
        call = client.return_value.responses.parse
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs['reasoning'], {'effort': 'low'})

    def test_partial_keywords_are_filled_without_replacing_valid_values(self):
        partial = output()
        partial.selected_object.product_type = '반팔 티셔츠'
        partial.selected_object.dominant_color = '네이비'
        partial.selected_object.pattern = '레터링'
        partial.selected_object.fit_or_shape = '오버핏'
        partial.search_keywords = vision.SearchKeywords(
            primary='네이비 레터링 반팔 티셔츠', alternatives=['', '오버핏 반팔티'], broad='그래픽 반팔티')
        result = vision.normalize_output(partial, 100, 80, (100, 80))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['search_keywords']['primary'], '네이비 레터링 반팔 티셔츠')
        self.assertEqual(result['search_keywords']['broad'], '그래픽 반팔티')
        self.assertTrue(result['search_keywords']['alternatives'][0])

    def test_unrecoverable_keywords_keep_identified_product(self):
        missing = output()
        missing.search_keywords = None
        missing.selected_object.product_type = None
        missing.selected_object.category = '의류'
        result = vision.normalize_output(missing, 100, 80, (100, 80))
        self.assertEqual(result['status'], 'search_unavailable')
        self.assertIsNone(result['search_keywords'])
        self.assertEqual(result['selected_object']['category'], '의류')

    def test_missing_key_returns_guidance_without_call(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(vision, "_call_vision") as call:
            with self.assertRaises(vision.VisionError) as caught:
                vision.analyze(image_bytes(), 100, 80, [40, 30], [[20, 10], [60, 10], [60, 50]])
        self.assertEqual(caught.exception.status_code, 503)
        call.assert_not_called()

    def test_route_returns_503_without_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = TestClient(app).post(
                "/api/circle-search/segment",
                data={"width": "100", "height": "80", "point": "[40,30]",
                      "box": "[20,10,60,50]",
                      "polygon": json.dumps([[20, 10], [60, 10], [60, 50], [20, 50]])},
                files={"image": ("test.jpg", image_bytes(), "image/jpeg")},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENAI_API_KEY", response.json()["detail"])

    def test_valid_geometry_maps_to_original_pixels(self):
        result = vision.normalize_output(
            output([[20, 10], [60, 10], [60, 50], [20, 50]]), 200, 160, (100, 80))
        self.assertEqual(result["geometry"]["polygon"],
                         [[40, 20], [120, 20], [120, 100], [40, 100]])
        self.assertFalse(result["geometry"]["needs_review"])

    def test_invalid_or_missing_polygon_is_not_displayed(self):
        for polygon in (None, [[20, 10], [60, 50], [20, 50], [60, 10]],
                        [[20, 10], [101, 10], [60, 50]]):
            result = vision.normalize_output(output(polygon), 100, 80, (100, 80))
            self.assertIsNone(result["geometry"]["polygon"])
            self.assertTrue(result["geometry"]["needs_review"])

    def test_no_object_has_no_geometry_or_keywords(self):
        result = vision.normalize_output(output(status="no_object"), 100, 80, (100, 80))
        self.assertEqual(result["status"], "no_object")
        self.assertIsNone(result["geometry"])
        self.assertIsNone(result["search_keywords"])

    def test_same_image_and_selection_calls_model_once(self):
        vision._cache.clear()
        fake = {"status": "success", "selected_object": {}, "geometry": None,
                "search_keywords": None,
                "metadata": {"model": "mock", "duration_ms": 1, "usage": {"total_tokens": 5}}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_VISION_MODEL": "mock"}), \
                patch.object(vision, "_call_vision", return_value=fake) as call:
            first = vision.analyze(image_bytes(), 100, 80, [40, 30], [[20, 10], [60, 10], [60, 50]])
            second = vision.analyze(image_bytes(), 100, 80, [40, 30], [[20, 10], [60, 10], [60, 50]])
        call.assert_called_once()
        self.assertFalse(first["metadata"]["cached"])
        self.assertTrue(second["metadata"]["cached"])

    def test_sdk_receives_one_marked_image_and_structured_schema(self):
        fake_response = SimpleNamespace(
            output_parsed=output([[20, 10], [60, 10], [60, 50], [20, 50]]),
            model="mock", usage=SimpleNamespace(input_tokens=100, output_tokens=40, total_tokens=140),
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(vision, "OpenAI") as client:
            client.return_value.responses.parse.return_value = fake_response
            result = vision._call_vision(image_bytes(), 100, 80, [40, 30],
                                         [[20, 10], [60, 10], [60, 50], [20, 50]], "mock")
        client.return_value.responses.parse.assert_called_once()
        args = client.return_value.responses.parse.call_args.kwargs
        self.assertIs(args["text_format"], vision.VisionOutput)
        self.assertIs(args["store"], False)
        self.assertEqual(sum(item["type"] == "input_image" for item in args["input"][0]["content"]), 1)
        self.assertEqual(result["metadata"]["usage"]["total_tokens"], 140)


if __name__ == "__main__":
    unittest.main()
