"""We Work Remotely category RSS feeds."""
import xml.etree.ElementTree as ET

from .base import Context, Source, SourceError, html_to_text, make_job

DEFAULT_FEEDS = ["remote-programming-jobs"]


class WeWorkRemotely(Source):
    key = "weworkremotely"
    label = "WWR"
    remote_board = True

    def fetch(self, ctx: Context):
        for feed in ctx.settings.get("feeds", DEFAULT_FEEDS):
            xml = ctx.http.text(f"https://weworkremotely.com/categories/{feed}.rss")
            try:
                items = ET.fromstring(xml.encode("utf-8")).iter("item")
            except ET.ParseError as exc:
                raise SourceError(f"feed {feed} is not valid RSS") from exc
            for item in items:
                yield from self._parse(item, ctx)

    def _parse(self, item: ET.Element, ctx: Context):
        """RSS titles look like 'Company: Job Title'."""
        raw_title = item.findtext("title", "")
        company, _, title = raw_title.partition(": ")
        if not title:
            company, title = "", raw_title
        if not ctx.wants_title(title):
            return
        yield make_job(
            title=title,
            company=company,
            url=item.findtext("link", "") or item.findtext("guid", ""),
            source=self.label,
            location=item.findtext("region", "") or "Remote",
            remote_type="remote",
            description=html_to_text(item.findtext("description", "")),
            date_posted=item.findtext("pubDate"),
        )
