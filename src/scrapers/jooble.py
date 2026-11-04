"""Jooble job search API. Optional: needs a free key from https://jooble.org/api/about
put in config/api_keys.json. Descriptions are short snippets.
"""
from .base import Context, Source, html_to_text, make_job


class Jooble(Source):
    key = "jooble"
    label = "Jooble"
    needs_key = True

    def fetch(self, ctx: Context):
        url = f"https://jooble.org/api/{ctx.api_key.get('api_key', '')}"
        for keyword in ctx.keywords:
            data = ctx.http.post_json(url, {"keywords": keyword,
                                            "location": ctx.settings.get("location", "Remote"),
                                            "page": 1})
            for item in data.get("jobs", []):
                if ctx.wants_title(item.get("title", "")):
                    yield make_job(
                        title=item.get("title", ""),
                        company=item.get("company", ""),
                        url=item.get("link", ""),
                        source=self.label,
                        location=item.get("location", ""),
                        description=html_to_text(item.get("snippet")),
                        salary_text=item.get("salary", ""),
                        date_posted=item.get("updated"),
                    )
