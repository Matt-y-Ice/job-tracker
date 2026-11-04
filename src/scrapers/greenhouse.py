"""Public Greenhouse job boards for the companies listed in sources.json."""
from .base import Context, html_to_text, infer_remote_type, make_job
from .company_boards import CompanyBoardSource


class Greenhouse(CompanyBoardSource):
    key = "greenhouse"
    label = "Greenhouse"

    def fetch_company(self, ctx: Context, slug: str, name: str):
        data = ctx.http.json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", content="true")
        for item in data.get("jobs", []):
            title = item.get("title", "")
            if not ctx.wants_title(title):
                continue
            location = (item.get("location") or {}).get("name", "")
            yield make_job(
                title=title,
                company=name or item.get("company_name") or slug,
                url=item.get("absolute_url", ""),
                source=self.label,
                location=location,
                remote_type=infer_remote_type(location, title, default="onsite"),
                description=html_to_text(item.get("content")),
                date_posted=item.get("first_published") or item.get("updated_at"),
            )
