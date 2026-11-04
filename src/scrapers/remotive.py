"""Remotive public API. Terms: link back, credit Remotive, and call at most 4 times a day."""
from .base import Context, Source, html_to_text, make_job


class Remotive(Source):
    key = "remotive"
    label = "Remotive"
    remote_board = True
    min_refresh_hours = 6  # Remotive asks for no more than 4 requests per day

    def fetch(self, ctx: Context):
        for category in ctx.settings.get("categories", ["software-dev"]):
            data = ctx.http.json("https://remotive.com/api/remote-jobs", category=category)
            for item in data.get("jobs", []):
                if not ctx.wants_title(item.get("title", "")):
                    continue
                yield make_job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    url=item.get("url", ""),
                    source=self.label,
                    location=item.get("candidate_required_location") or "Remote",
                    remote_type="remote",
                    description=html_to_text(item.get("description")),
                    salary_text=item.get("salary", ""),
                    date_posted=item.get("publication_date"),
                )
