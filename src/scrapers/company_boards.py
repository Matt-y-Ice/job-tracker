"""Shared logic for sources that read one public job board per company (Greenhouse, Lever, Ashby)."""
from .base import Context, Source, SourceError, log


class CompanyBoardSource(Source):
    """Loops over `companies` in sources.json ({"slug": "Display Name"}).

    One company failing (renamed board, network blip) is logged and skipped;
    the source only fails if every company fails.
    """

    def fetch(self, ctx: Context):
        companies = ctx.settings.get("companies", {})
        if isinstance(companies, list):
            companies = {slug: "" for slug in companies}
        failed = []
        for slug, name in companies.items():
            try:
                yield from self.fetch_company(ctx, slug, name)
            except SourceError as exc:
                log.warning("%s board '%s' failed: %s", self.label, slug, exc)
                failed.append(slug)
        if failed:
            self.warnings.append(f"{len(failed)} of {len(companies)} boards failed: {', '.join(failed)}")
            if len(failed) == len(companies):
                raise SourceError("every company board failed")

    def fetch_company(self, ctx: Context, slug: str, name: str):
        """Yield jobs for one company."""
        raise NotImplementedError
