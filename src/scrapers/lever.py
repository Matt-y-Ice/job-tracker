"""Public Lever job boards for the companies listed in sources.json."""
from .base import Context, annualize, html_to_text, infer_remote_type, make_job
from .company_boards import CompanyBoardSource

WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


class Lever(CompanyBoardSource):
    key = "lever"
    label = "Lever"

    def fetch_company(self, ctx: Context, slug: str, name: str):
        data = ctx.http.json(f"https://api.lever.co/v0/postings/{slug}", mode="json")
        for item in data:
            title = item.get("text", "")
            if not ctx.wants_title(title):
                continue
            location = (item.get("categories") or {}).get("location", "")
            salary = item.get("salaryRange") or {}
            usd = salary.get("currency", "USD") == "USD"
            yield make_job(
                title=title,
                company=name or slug.title(),
                url=item.get("hostedUrl", ""),
                source=self.label,
                location=location,
                remote_type=WORKPLACE.get(item.get("workplaceType", ""))
                or infer_remote_type(location, title, default="onsite"),
                mode_hint=WORKPLACE.get(item.get("workplaceType", "")),
                description=self._description(item),
                salary_min=annualize(salary.get("min"), salary.get("interval")) if usd else None,
                salary_max=annualize(salary.get("max"), salary.get("interval")) if usd else None,
                date_posted=item.get("createdAt"),
            )

    @staticmethod
    def _description(item: dict) -> str:
        """Lever splits the posting into an intro, titled lists, and a closing section."""
        parts = [item.get("descriptionPlain", "")]
        for section in item.get("lists") or []:
            parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content'))}")
        parts.append(item.get("additionalPlain", ""))
        return "\n\n".join(p.strip() for p in parts if p and p.strip())
