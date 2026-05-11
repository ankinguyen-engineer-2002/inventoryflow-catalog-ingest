"""Mock provider — deterministic fixtures for tests + safety fallback."""

from __future__ import annotations

from .provider import (
    EnrichmentMeta,
    EnrichmentRequest,
    EnrichmentResponse,
)

KNOWN_TRANSLATIONS: dict[str, str] = {
    "把套": "handlebar grip",
    "组合开关": "multi-function switch",
    "钢制方向把": "steel handlebar",
    "护套芯": "padding insert",
    "熄火开关": "stop switch",
    "油门线": "throttle cable",
    "加速器": "accelerator",
    "塑料扎带": "plastic cable tie",
    "风门线": "choke cable",
}


class MockProvider:
    name = "mock"

    async def enrich(self, req: EnrichmentRequest) -> EnrichmentResponse:
        result: str | list[int] | None = None
        if req.field == "translate_cn_to_en":
            cn = req.inputs.get("cn") or ""
            result = KNOWN_TRANSLATIONS.get(cn, f"[mock-translate: {cn[:20]}]")
        elif req.field == "infer_make":
            result = "Kayo"
        elif req.field == "extract_callouts":
            result = []

        return EnrichmentResponse(
            id=req.id,
            field=req.field,
            result=result,
            confidence="low",
            meta=EnrichmentMeta(
                provider=self.name,
                prompt_template_ver="mock-v1",
                cache_hit=False,
            ),
        )
