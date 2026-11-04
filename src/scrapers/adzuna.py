"""Adzuna job search API (US). Optional: needs a free app_id and app_key from
https://developer.adzuna.com put in config/api_keys.json. Descriptions are short snippets.
"""
from .base import Context, Source, make_job


class Adzuna(Source):
    key = "adzuna"
    label = "Adzuna"
    needs_key = True

    def fetch(self, ctx: Context):
        country = ctx.settings.get("country", "us")
        for keyword in ctx.keywords:
            for page in range(1, int(ctx.settings.get("max_pages_per_keyword", 1)) + 1):
                data = ctx.http.json(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
                    app_id=ctx.api_key.get("app_id", ""), app_key=ctx.api_key.get("app_key", ""),
                    what=keyword, what_or=ctx.settings.get("what_or", "remote"),
                    max_days_old=ctx.days_back, results_per_page=50,
                    **{"content-type": "application/json"})
                results = data.get("results", [])
                for item in results:
                    if ctx.wants_title(item.get("title", "")):
                        yield self._job(item)
                if len(results) < 50:
                    break

    def _job(self, item: dict) -> dict:
        predicted = str(item.get("salary_is_predicted", "0")) == "1"  # Adzuna's guess, not the employer's
        return make_job(
            title=item.get("title", ""),
            company=(item.get("company") or {}).get("display_name", ""),
            url=item.get("redirect_url", ""),
            source=self.label,
            location=(item.get("location") or {}).get("display_name", ""),
            description=item.get("description", ""),
            salary_min=None if predicted else item.get("salary_min"),
            salary_max=None if predicted else item.get("salary_max"),
            date_posted=item.get("created"),
        )
