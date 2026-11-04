"""Public Ashby job boards for the companies listed in sources.json."""
from .base import Context, annualize, html_to_text, infer_remote_type, make_job
from .company_boards import CompanyBoardSource

WORKPLACE = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}


class Ashby(CompanyBoardSource):
    key = "ashby"
    label = "Ashby"

    def fetch_company(self, ctx: Context, slug: str, name: str):
        data = ctx.http.json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                             includeCompensation="true")
        for item in data.get("jobs", []):
            title = item.get("title", "")
            if not item.get("isListed", True) or not ctx.wants_title(title):
                continue
            location = item.get("location", "")
            low, high = self._salary(item.get("compensation") or {})
            remote = WORKPLACE.get(item.get("workplaceType") or "")
            if not remote and item.get("isRemote"):
                remote = "remote"
            yield make_job(
                title=title,
                company=name or slug.title(),
                url=item.get("jobUrl", ""),
                source=self.label,
                location=location,
                remote_type=remote or infer_remote_type(location, title, default="onsite"),
                mode_hint=remote,
                description=html_to_text(item.get("descriptionHtml")),
                salary_min=low,
                salary_max=high,
                salary_text=(item.get("compensation") or {}).get("compensationTierSummary", "") or "",
                date_posted=item.get("publishedAt"),
            )

    @staticmethod
    def _salary(comp: dict) -> tuple[int | None, int | None]:
        """Yearly USD base salary from Ashby's compensation components."""
        for part in comp.get("summaryComponents") or []:
            if part.get("compensationType") == "Salary" and part.get("currencyCode") == "USD":
                interval = (part.get("interval") or "1 YEAR").lower()
                return (annualize(part.get("minValue"), interval),
                        annualize(part.get("maxValue"), interval))
        return None, None
