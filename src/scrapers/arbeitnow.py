"""Arbeitnow public job board API (mostly Europe). Only remote postings are kept."""
from .base import Context, Source, html_to_text, make_job


class Arbeitnow(Source):
    key = "arbeitnow"
    label = "Arbeitnow"
    remote_board = True

    def fetch(self, ctx: Context):
        for page in range(1, int(ctx.settings.get("pages", 2)) + 1):
            data = ctx.http.json("https://www.arbeitnow.com/api/job-board-api", page=page)
            for item in data.get("data", []):
                if not item.get("remote") or not ctx.wants_title(item.get("title", "")):
                    continue
                yield make_job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    url=item.get("url", ""),
                    source=self.label,
                    location=item.get("location") or "Remote",
                    remote_type="remote",
                    description=html_to_text(item.get("description")),
                    date_posted=item.get("created_at"),
                )
            if not (data.get("links") or {}).get("next"):
                break
