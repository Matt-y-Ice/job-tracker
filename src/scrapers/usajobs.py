"""USAJOBS (US federal government) search API, remote jobs only. Optional: needs a free
API key from https://developer.usajobs.gov plus your email, put in config/api_keys.json.
"""
from .base import Context, Source, annualize, make_job

RATE_PERIODS = {"PA": "year", "PH": "hour", "PM": "month", "BW": "week"}


class USAJobs(Source):
    key = "usajobs"
    label = "USAJOBS"
    needs_key = True

    def fetch(self, ctx: Context):
        headers = {"Host": "data.usajobs.gov", "User-Agent": ctx.api_key.get("email", ""),
                   "Authorization-Key": ctx.api_key.get("api_key", "")}
        for keyword in ctx.keywords:
            data = ctx.http.json("https://data.usajobs.gov/api/search", headers=headers,
                                 Keyword=keyword, RemoteIndicator="True",
                                 DatePosted=min(ctx.days_back, 60), ResultsPerPage=100)
            for item in (data.get("SearchResult") or {}).get("SearchResultItems", []):
                job = item.get("MatchedObjectDescriptor") or {}
                if ctx.wants_title(job.get("PositionTitle", "")):
                    yield self._job(job)

    def _job(self, job: dict) -> dict:
        pay = (job.get("PositionRemuneration") or [{}])[0]
        period = RATE_PERIODS.get(pay.get("RateIntervalCode"), "year")
        details = (job.get("UserArea") or {}).get("Details") or {}
        duties = details.get("MajorDuties") or []
        description = "\n\n".join(filter(None, [
            details.get("JobSummary", ""), "\n".join(duties if isinstance(duties, list) else [duties]),
            job.get("QualificationSummary", "")]))
        return make_job(
            title=job.get("PositionTitle", ""),
            company=job.get("OrganizationName", ""),
            url=job.get("PositionURI", ""),
            source=self.label,
            location=job.get("PositionLocationDisplay", "") or "United States",
            description=description,
            salary_min=annualize(pay.get("MinimumRange"), period),
            salary_max=annualize(pay.get("MaximumRange"), period),
            date_posted=job.get("PublicationStartDate"),
            mode_hint="remote",  # searched with RemoteIndicator=True
        )
