"""The Muse public jobs API (works without a key at up to 500 requests per hour)."""
from .base import Context, Source, html_to_text, make_job


class TheMuse(Source):
    key = "themuse"
    label = "The Muse"

    def fetch(self, ctx: Context):
        location = ctx.settings.get("location", "Flexible / Remote")
        for category in ctx.settings.get("categories", ["Software Engineering"]):
            for page in range(int(ctx.settings.get("max_pages", 5))):
                data = ctx.http.json("https://www.themuse.com/api/public/jobs",
                                     page=page, category=category, location=location)
                for item in data.get("results", []):
                    if ctx.wants_title(item.get("name", "")):
                        yield self._job(item)
                if page + 1 >= int(data.get("page_count") or 0):
                    break

    def _job(self, item: dict) -> dict:
        places = "; ".join(loc.get("name", "") for loc in item.get("locations") or [])
        return make_job(
            title=item.get("name", ""),
            company=(item.get("company") or {}).get("name", ""),
            url=(item.get("refs") or {}).get("landing_page", ""),
            source=self.label,
            location=places,
            description=html_to_text(item.get("contents")),
            date_posted=item.get("publication_date"),
        )
