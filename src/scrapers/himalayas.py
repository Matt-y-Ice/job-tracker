"""Himalayas public jobs API (search endpoint, 20 results per page)."""
from .base import Context, Source, annualize, html_to_text, make_job


class Himalayas(Source):
    key = "himalayas"
    label = "Himalayas"
    remote_board = True

    def fetch(self, ctx: Context):
        pages = int(ctx.settings.get("max_pages_per_keyword", 2))
        for keyword in ctx.keywords:
            for page in range(1, pages + 1):
                data = ctx.http.json("https://himalayas.app/jobs/api/search", q=keyword, page=page)
                jobs = data.get("jobs", [])
                for item in jobs:
                    if ctx.wants_title(item.get("title", "")):
                        yield self._job(item)
                if len(jobs) < 20:
                    break

    def _job(self, item: dict) -> dict:
        """Salaries are only kept as numbers when they are in US dollars."""
        usd = (item.get("currency") or "USD") == "USD"
        period = item.get("salaryPeriod")
        low = annualize(item.get("minSalary"), period) if usd else None
        high = annualize(item.get("maxSalary"), period) if usd else None
        text = ""
        if not usd and item.get("minSalary"):
            text = f"{item['currency']} {item['minSalary']:,}-{item.get('maxSalary') or ''} ({period})"
        places = item.get("locationRestrictions") or []
        return make_job(
            title=item.get("title", ""),
            company=item.get("companyName", ""),
            url=item.get("guid") or item.get("applicationLink", ""),
            source=self.label,
            location="Remote" + (f" ({', '.join(places)})" if places else ", worldwide"),
            remote_type="remote",
            description=html_to_text(item.get("description")),
            salary_min=low,
            salary_max=high,
            salary_text=text,
            date_posted=item.get("pubDate"),
        )
