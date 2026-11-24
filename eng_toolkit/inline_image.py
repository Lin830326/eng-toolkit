"""The ``inline_image`` parameter of the tools that write a PNG, shared by the domains and the gateway (``gateway/images.py``)."""

from __future__ import annotations

PARAM = "inline_image"
INLINE_IMAGE_MAX_BYTES = 600_000  # 600 kB; base64 makes the block a third larger
INLINE_IMAGE_DESCRIPTION = "true (default): the PNG also comes back as an image block in the reply when it is <= 600 kB; false: path only."


def skipped_warning(size: int) -> str:
    return (
        f"The PNG is {size / 1000:.0f} kB, above the {INLINE_IMAGE_MAX_BYTES // 1000} kB limit for an inline image, so no image is "
        "attached to this reply. The file is complete: open it from data.png_path / artifacts[]."
    )


__all__ = ["INLINE_IMAGE_DESCRIPTION", "INLINE_IMAGE_MAX_BYTES", "PARAM", "skipped_warning"]
