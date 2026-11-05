"""Playwright route helper: abort images, media, fonts and known trackers; allowlist wins."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.runtime import Watch

DEFAULT_BLOCKED_TYPES: tuple[str, ...] = ("image", "media", "font")


class BlockPolicy(BaseModel):
    """What to block. ``allow`` patterns (glob on URL) always win over blocks."""

    model_config = ConfigDict(extra="forbid")
    resource_types: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCKED_TYPES))
    block_trackers: bool = True
    block_url_globs: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)


def install_route_blocker(
    context_or_page: Any, watch: Watch, policy: BlockPolicy | None = None
) -> None:
    """``page.route("**/*", handler)``; blocked requests are counted with an estimated size."""
    raise NotImplementedError("build-progress item 21")


async def install_route_blocker_async(
    context_or_page: Any, watch: Watch, policy: BlockPolicy | None = None
) -> None:
    """Async API variant."""
    raise NotImplementedError("build-progress item 21")
