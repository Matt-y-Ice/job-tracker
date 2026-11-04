"""Remote OK public API. Terms: link back to the Remote OK URL and name Remote OK as the source."""
from .base import Context, Source, html_to_text, make_job


class RemoteOK(Source):
    key = "remoteok"
    label = "RemoteOK"
    remote_board = True

    def fetch(self, ctx: Context):
        data = ctx.http.json("https://remoteok.com/api")
        for item in data[1:]:  # item 0 is the legal notice
            title = item.get("position", "")
            if not ctx.wants_title(title):
                continue
            yield make_job(
                title=title,
                company=item.get("company", ""),
                url=item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('slug', '')}",
                source=self.label,
                location=item.get("location") or "Remote",
                remote_type="remote",
                description=html_to_text(item.get("description")),
                salary_min=item.get("salary_min") or None,
                salary_max=item.get("salary_max") or None,
                date_posted=item.get("epoch") or item.get("date"),
            )
