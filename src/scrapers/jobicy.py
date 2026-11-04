"""Jobicy public remote jobs API. Terms: credit Jobicy and keep the Jobicy listing URL."""
from .base import Context, Source, annualize, html_to_text, make_job


class Jobicy(Source):
    key = "jobicy"
    label = "Jobicy"
    remote_board = True

    def fetch(self, ctx: Context):
        for industry in ctx.settings.get("industries", ["engineering"]):
            params = {"count": 100, "industry": industry}
            if ctx.settings.get("geo"):
                params["geo"] = ctx.settings["geo"]
            data = ctx.http.json("https://jobicy.com/api/v2/remote-jobs", **params)
            for item in data.get("jobs", []):
                if ctx.wants_title(item.get("jobTitle", "")):
                    yield self._job(item)

    def _job(self, item: dict) -> dict:
        usd = (item.get("salaryCurrency") or "USD") == "USD"
        period = item.get("salaryPeriod") or "year"
        low = annualize(item.get("salaryMin") or item.get("annualSalaryMin"), period) if usd else None
        high = annualize(item.get("salaryMax") or item.get("annualSalaryMax"), period) if usd else None
        return make_job(
            title=item.get("jobTitle", ""),
            company=item.get("companyName", ""),
            url=item.get("url", ""),
            source=self.label,
            location=f"Remote ({item['jobGeo']})" if item.get("jobGeo") else "Remote",
            remote_type="remote",
            description=html_to_text(item.get("jobDescription")),
            salary_min=low,
            salary_max=high,
            date_posted=item.get("pubDate"),
        )
