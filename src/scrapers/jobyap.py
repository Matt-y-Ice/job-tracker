"""JobYap public JSON API: tech jobs from ~900 companies' career sites. No key needed.

The list endpoint has no descriptions, so each new job costs one extra request.
JobYap asks for small pages and no unbounded bulk pulls, so new detail requests are
capped per run (max_details) and descriptions already downloaded are reused from the last run.
"""
from datetime import date, timedelta

from .base import Context, Source, annualize, make_job, to_date

API = "https://jobyap.com/api/v1"


class JobYap(Source):
    key = "jobyap"
    label = "JobYap"

    def fetch(self, ctx: Context):
        window = "24h" if ctx.days_back <= 1 else "7d" if ctx.days_back <= 7 else "30d"
        cutoff = (date.today() - timedelta(days=ctx.days_back)).isoformat()
        details_left = int(ctx.settings.get("max_details", 120))
        seen, skipped = set(), 0
        for keyword in ctx.keywords:
            cursor = None
            for _ in range(int(ctx.settings.get("max_pages_per_keyword", 3))):
                params = {"q": keyword, "posted_within": window, "limit": 20,
                          "work_mode": ctx.settings.get("work_mode", "remote"),
                          "location": ctx.settings.get("location", "country-US")}
                if cursor:
                    params["cursor"] = cursor
                data = ctx.http.json(f"{API}/jobs", **params)
                for item in data.get("jobs", []):
                    if item["id"] in seen or not ctx.wants_title(item.get("title", "")):
                        continue
                    seen.add(item["id"])
                    if (to_date(item.get("posted_at")) or cutoff) < cutoff:
                        continue
                    if details_left <= 0 and str(item["id"]) not in ctx.detail_cache:
                        skipped += 1
                        continue
                    payload, downloaded = ctx.detail(
                        item["id"], lambda: ctx.http.json(f"{API}/jobs/{item['id']}"))
                    details_left -= downloaded
                    yield self._job(payload)
                cursor = data.get("next_cursor")
                if not cursor:
                    break
        if skipped:
            self.warnings.append(f"{skipped} new jobs left for the next run (max_details reached)")

    def _job(self, item: dict) -> dict:
        # Each location has its own work mode. Keep the remote ones (this search asked for remote),
        # marked so the classifier sees them as remote; fall back to all locations if none are.
        places = item.get("locations") or []
        remote = [p for p in places if p.get("work_mode") == "remote"]
        location = "; ".join(f"{p.get('display') or p.get('country_code', '')}"
                             f"{' (remote)' if p in remote else ''}" for p in (remote or places))
        pay = next((s for s in item.get("salaries") or [] if s.get("currency") == "USD"), {})
        return make_job(
            title=item.get("title", ""),
            company=item.get("company", ""),
            url=item.get("url", ""),
            source=self.label,
            location=location,
            description=item.get("description_markdown", ""),
            salary_min=annualize(pay.get("min"), pay.get("period")),
            salary_max=annualize(pay.get("max"), pay.get("period")),
            date_posted=item.get("posted_at"),
        )
