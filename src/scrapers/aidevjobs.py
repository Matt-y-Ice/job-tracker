"""AI Dev Jobs (aidevboard.com) public RSS feed: AI and ML engineering roles.

Off by default: every role is AI/ML focused. Turn it on in sources.json if you want them.
The feed has a one-line summary per job, not the full description.
"""
import re
import xml.etree.ElementTree as ET

from .base import Context, Source, SourceError, make_job

MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


class AIDevJobs(Source):
    key = "aidevjobs"
    label = "AI Dev Jobs"
    enabled_by_default = False

    def fetch(self, ctx: Context):
        xml = ctx.http.text("https://aidevboard.com/feed.xml")
        try:
            items = ET.fromstring(xml.encode("utf-8")).iter("item")
        except ET.ParseError as exc:
            raise SourceError("feed is not valid RSS") from exc
        for item in items:
            job = self._job(item)
            if job and ctx.wants_title(job["title"]):
                yield job

    def _job(self, item: ET.Element) -> dict | None:
        """Titles look like 'Role at Company ($145k-$180k)'; summaries like 'Role — City · full-time · Hybrid · $145k'."""
        raw = re.sub(r"\s*\(\$[^)]*\)\s*$", "", item.findtext("title", ""))
        title, _, company = raw.rpartition(" at ")
        if not title:
            return None
        summary = item.findtext("description", "")
        parts = [p.strip() for p in summary.split(" — ", 1)[-1].split("·")]
        mode = next((MODES[p.lower()] for p in parts if p.lower() in MODES), None)
        return make_job(
            title=title,
            company=company,
            url=item.findtext("link", ""),
            source=self.label,
            location=parts[0] if parts else "",
            description=f"{summary}\nTopics: {item.findtext('category', '')}",
            salary_text=next((p for p in parts if p.startswith("$")), ""),
            date_posted=item.findtext("pubDate"),
            mode_hint=mode,
        )
