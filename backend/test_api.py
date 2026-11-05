import io
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from main import app


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(output, format="JPEG")
    return output.getvalue()


class CircleSearchApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.data = {
            "width": "100",
            "height": "80",
            "point": json.dumps([40, 30]),
            "box": json.dumps([20, 10, 60, 50]),
            "polygon": json.dumps([[20, 10], [60, 10], [60, 50], [20, 50]]),
        }

    def post(self, data=None, content_type="image/jpeg", content=None):
        return self.client.post(
            "/api/circle-search/segment",
            data=data or self.data,
            files={"image": ("test.jpg", content if content is not None else image_bytes(), content_type)},
        )

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_valid_selection(self):
        with patch("routers.circle_search.analyze", return_value={
            "status": "success", "selected_object": {"product_type": "sneaker"},
            "geometry": {"bounding_box": [20, 10, 60, 50], "polygon": [[20, 10], [60, 10], [60, 50], [20, 50]],
                         "geometry_type": "estimated", "needs_review": False},
            "search_keywords": {"primary": "흰 운동화", "alternatives": ["흰 신발", "스니커즈"], "broad": "운동화"},
            "metadata": {"model": "mock", "duration_ms": 1, "usage": {"total_tokens": 10}},
        }) as analyze:
            response = self.post()
        analyze.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["selection"]["box"], [20, 10, 60, 50])
        self.assertFalse(response.json()["sam_enabled"])

    def test_rejects_mismatched_dimensions(self):
        self.assertEqual(self.post({**self.data, "width": "101"}).status_code, 422)

    def test_rejects_out_of_bounds_polygon(self):
        data = {**self.data, "polygon": json.dumps([[20, 10], [100, 10], [60, 50]])}
        self.assertEqual(self.post(data).status_code, 422)

    def test_rejects_bad_file_type(self):
        self.assertEqual(self.post(content_type="text/plain").status_code, 415)

    def test_rejects_large_file(self):
        self.assertEqual(self.post(content=b"x" * (10 * 1024 * 1024 + 1)).status_code, 413)


if __name__ == "__main__":
    unittest.main()
