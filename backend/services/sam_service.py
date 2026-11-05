from typing import Protocol


class SamInference(Protocol):
    """Future server-side SAM adapter. Coordinates use original image pixels."""

    async def segment(
        self,
        image_bytes: bytes,
        point: tuple[int, int],
        box: tuple[int, int, int, int],
    ) -> bytes:
        """Return a segmentation mask encoded as image bytes."""
        ...


# TODO: Implement a server-side SAM 2.1 / SAM 3 API adapter here.
# Keep its API key on the server. Convert returned mask into a documented
# response field only after a real inference service is connected.
