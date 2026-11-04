"""Findwork.dev jobs API, remote jobs only. Optional: needs a free key from
https://findwork.dev/developers/ put in config/api_keys.json.
"""
from .base import Context, Source, html_to_text, make_job


class Findwork(Source):
    key = "findwork"
    label = "Findwork"
    needs_key = True

    def fetch(self, ctx: Context):
        headers = {"Authorization": f"Token {ctx.api_key.get('api_key', '')}"}
        for keyword in ctx.keywords:
            data = ctx.http.json("https://findwork.dev/api/jobs/", headers=headers,
                                 search=keyword, remote="true", order_by="-date")
            for item in data.get("results", []):
                if ctx.wants_title(item.get("role", "")):
                    yield make_job(
                        title=item.get("role", ""),
                        company=item.get("company_name", ""),
                        url=item.get("url", ""),
                        source=self.label,
                        location=item.get("location") or "",
                        description=html_to_text(item.get("text")),
                        date_posted=item.get("date_posted"),
                        mode_hint="remote" if item.get("remote") else None,
                    )
