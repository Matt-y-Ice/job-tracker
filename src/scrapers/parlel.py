"""Parlel public roles API. No key needed (limited to 50 requests per minute).

Lists are newest first and have no descriptions. A job whose location is clearly
outside the US is kept without downloading its description; others cost one extra
request, capped per run (max_details) and reused from the last run.
"""
from datetime import date, timedelta

from .base import Context, Source, annualize, html_to_text, make_job, to_date
from .location import read_location

API = "https://api.parlel.com/api/public/roles"


class Parlel(Source):
    key = "parlel"
    label = "Parlel"

    def fetch(self, ctx: Context):
        cutoff = (date.today() - timedelta(days=ctx.days_back)).isoformat()
        details_left = int(ctx.settings.get("max_details", 60))
        seen, skipped = set(), 0
        for keyword in ctx.keywords:
            cursor = None
            for _ in range(int(ctx.settings.get("max_pages_per_keyword", 2))):
                params = {"q": keyword, "remote": ctx.settings.get("remote", "remote"), "limit": 60}
                if cursor:
                    params["cursor"] = cursor
                data = ctx.http.json(API, **params)
                too_old = False
                for item in data.get("items", []):
                    if (to_date(item.get("published_at")) or cutoff) < cutoff:
                        too_old = True  # newest first, so the rest are older too
                        break
                    if item["id"] in seen or not ctx.wants_title(item.get("title", "")):
                        continue
                    seen.add(item["id"])
                    if read_location(self._location(item))[0] <= -2:
                        yield self._job(item)  # clearly outside the US: no need for the description
                    elif details_left > 0 or item["id"] in ctx.detail_cache:
                        payload, downloaded = ctx.detail(
                            item["id"], lambda: ctx.http.json(item["role_url"] + ".json"))
                        details_left -= downloaded
                        yield self._job(payload)
                    else:
                        skipped += 1
                cursor = data.get("next_cursor")
                if too_old or not cursor:
                    break
        if skipped:
            self.warnings.append(f"{skipped} new jobs left for the next run (max_details reached)")

    @staticmethod
    def _location(item: dict) -> str:
        parts = [item.get("location"), item.get("address_region"), item.get("address_country")]
        return ", ".join(dict.fromkeys(p for p in parts if p))

    def _job(self, item: dict) -> dict:
        usd = item.get("comp_currency") in (None, "USD")
        period = item.get("comp_period")
        return make_job(
            title=item.get("title", ""),
            company=(item.get("company") or {}).get("name", ""),
            url=item.get("role_url", ""),
            source=self.label,
            location=self._location(item),
            description=html_to_text(item.get("description") or ""),
            salary_min=annualize(item.get("comp_min"), period) if usd else None,
            salary_max=annualize(item.get("comp_max"), period) if usd else None,
            date_posted=item.get("published_at"),
            mode_hint=item.get("remote"),
        )
