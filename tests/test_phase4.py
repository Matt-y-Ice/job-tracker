"""Phase 4 tests: building the workbook and, above all, merging without losing tracking data."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from src import excel_builder as xb


def ranked_job(jid, score, bucket="main", title=None, **extra):
    job = {"id": jid, "title": title or f"Engineer {jid}", "company": f"Co {jid}", "location": "Remote - USA",
           "remote_type": "remote", "salary_text": "$120K-$150K", "source": "Greenhouse",
           "date_posted": "2026-10-20", "url": f"https://jobs.example.com/{jid}", "bucket": bucket,
           "score": score, "reasons": ["Strong Python match."], "missing_skills": ["Go"],
           "red_flags": [], "flag_reasons": [], "location_note": "Remote, open to US: test",
           "score_breakdown": {"skill": 30, "experience": 15, "role_fit": 15, "pay": 10, "constraints": 10}}
    job.update(extra)
    return job


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.ranked = self.dir / "jobs_ranked.json"
        self.out = self.dir / "Job_Rankings.xlsx"

    def build(self, jobs, when="2026-10-29T08:00:00"):
        self.ranked.write_text(json.dumps({"ranked_at": when, "model": "m", "jobs": jobs,
                                           "weights": {"llm": 0.5}}), encoding="utf-8")
        return xb.build(self.ranked, self.out)

    def sheet_rows(self, wb, name):
        ws = wb[name]
        headers = [c.value for c in ws[1]]
        return {r[headers.index("Job ID")]: dict(zip(headers, r))
                for r in ws.iter_rows(min_row=2, values_only=True) if r[headers.index("Job ID")]}

    def edit(self, sheet, job_id, **changes):
        """Simulate typing into the workbook in Excel."""
        wb = load_workbook(self.out)
        ws = wb[sheet]
        headers = [c.value for c in ws[1]]
        row = next(r for r in range(2, ws.max_row + 1)
                   if ws.cell(row=r, column=headers.index("Job ID") + 1).value == job_id)
        for header, value in changes.items():
            ws.cell(row=row, column=headers.index(header) + 1, value=value)
        wb.save(self.out)

    def test_first_build_layout_and_routing(self):
        result = self.build([
            ranked_job("a", 74), ranked_job("b", 30),
            ranked_job("c", 0, flag_reasons=["Deal breaker in title: 'AI/ML development'"]),
            ranked_job("u", 61, bucket="unsure", location_note="Unclear if remote: test"),
            ranked_job("x", 0, bucket="excluded", flag_reasons=["Not remote US. Remote, not open to US: Poland"])])
        self.assertEqual(result.counts, {xb.TOP: 1, xb.LOW: 2, xb.UNSURE: 1, xb.EXCLUDED: 1})
        wb = load_workbook(self.out)
        self.assertEqual(wb.sheetnames, list(xb.SHEET_ORDER))
        top = wb[xb.TOP]
        self.assertEqual(top["A1"].fill.start_color.rgb, "FF1F3864")
        self.assertTrue(top["A1"].font.bold)
        self.assertEqual((top.freeze_panes, top.auto_filter.ref), ("D2", "A1:AF2"))
        self.assertEqual(top["O2"].hyperlink.target, "https://jobs.example.com/a")
        self.assertEqual(top["S2"].value, "New")
        self.assertEqual(top["Z2"].value, '=IF(T2="","",TODAY()-T2)')
        low = self.sheet_rows(wb, xb.LOW)
        self.assertTrue(low["c"]["Why It Matched"].startswith("Filtered out: Deal breaker"))
        self.assertIn("Deal breaker in title", low["c"]["Red Flags"])
        self.assertEqual(self.sheet_rows(wb, xb.UNSURE)["u"]["Location Check"], "Unclear if remote: test")
        self.assertIn("Poland", self.sheet_rows(wb, xb.EXCLUDED)["x"]["Reason"])
        lists = [dv.formula1 for dv in top.data_validations.dataValidation]
        self.assertIn('"New,Interested,Applied,Interviewing,Offer,Rejected,Not Interested"', lists)

    def test_merge_keeps_tracking_and_moves_across_50(self):
        self.build([ranked_job("a", 74), ranked_job("b", 30), ranked_job("gone", 60)])
        self.edit(xb.TOP, "a", Status="Applied", **{"Date Applied": datetime(2026, 10, 25),
                                                    "Notes": "Referred by Sam", "Applied Via": "Referral"})
        self.edit(xb.LOW, "b", Status="Interested", Hired="No", **{"Next Step": "Email recruiter"})
        self.edit(xb.TOP, "gone", Notes="Liked this one")

        self.build([ranked_job("a", 40), ranked_job("b", 80), ranked_job("new", 55)], when="2026-11-02T08:00:00")
        wb = load_workbook(self.out)
        top, low = self.sheet_rows(wb, xb.TOP), self.sheet_rows(wb, xb.LOW)
        self.assertEqual(set(top), {"b", "new", "gone"})
        self.assertEqual(set(low), {"a"})
        self.assertEqual((low["a"]["Status"], low["a"]["Notes"], low["a"]["Applied Via"]),
                         ("Applied", "Referred by Sam", "Referral"))
        self.assertEqual(low["a"]["Date Applied"], datetime(2026, 10, 25))
        self.assertEqual(low["a"]["Score"], 40)
        self.assertEqual((top["b"]["Status"], top["b"]["Next Step"]), ("Interested", "Email recruiter"))
        self.assertEqual(top["new"]["Status"], "New")
        self.assertEqual(top["gone"]["Notes"], "Liked this one")              # no longer listed: kept
        self.assertEqual(top["gone"]["Last Seen"], datetime(2026, 10, 29))   # ...with its last-seen date
        self.assertEqual(top["b"]["Last Seen"], datetime(2026, 11, 2))
        self.assertEqual([r[0] for r in wb[xb.TOP].iter_rows(min_row=2, max_col=1, values_only=True)],
                         [1, 2, 3])                                          # re-ranked by score

    def test_user_columns_rows_and_sheets_survive(self):
        self.build([ranked_job("a", 74)])
        wb = load_workbook(self.out)
        ws = wb[xb.TOP]
        ws.cell(row=1, column=ws.max_column + 1, value="My Rating")
        ws.cell(row=2, column=ws.max_column, value=5)
        ws.cell(row=3, column=3, value="Job I found myself")   # hand-added row, no Job ID
        ws.cell(row=3, column=15, value="Ask Alex for the link")
        wb.create_sheet("My Notes")["A1"] = "keep me"
        wb.save(self.out)

        self.build([ranked_job("a", 70)])
        wb = load_workbook(self.out)
        headers = [c.value for c in wb[xb.TOP][1]]
        self.assertEqual(headers[-1], "My Rating")
        rows = list(wb[xb.TOP].iter_rows(min_row=2, values_only=True))
        self.assertEqual(rows[0][-1], 5)
        manual = next(r for r in rows if r[2] == "Job I found myself")
        self.assertEqual(manual[14], "Ask Alex for the link")             # not turned into a broken link
        self.assertEqual(wb["My Notes"]["A1"].value, "keep me")

    def test_tracked_job_never_moves_to_excluded(self):
        self.build([ranked_job("a", 74)])
        self.edit(xb.TOP, "a", Status="Applied")
        self.build([ranked_job("a", 0, bucket="excluded", flag_reasons=["Not remote US"])])
        wb = load_workbook(self.out)
        self.assertEqual(self.sheet_rows(wb, xb.LOW)["a"]["Status"], "Applied")
        self.assertNotIn("a", self.sheet_rows(wb, xb.EXCLUDED))

    def test_locked_workbook_gives_clear_error_and_keeps_file(self):
        self.build([ranked_job("a", 74)])
        before = self.out.read_bytes()
        with mock.patch.object(xb.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(xb.WorkbookLocked, "Close the workbook in Excel"):
                self.build([ranked_job("a", 10)])
        self.assertEqual(self.out.read_bytes(), before)
        self.assertFalse(list(self.dir.glob("~tmp_*")))

    def test_backups_keep_last_ten(self):
        for i in range(13):
            self.build([ranked_job("a", 50 + i)])
        backups = list((self.dir / "backups").glob("Job_Rankings_*.xlsx"))
        self.assertEqual(len(backups), 10)

    def test_illegal_characters_are_removed(self):
        self.build([ranked_job("a", 74, title="Engineer\x07 II")])
        self.assertEqual(load_workbook(self.out)[xb.TOP]["C2"].value, "Engineer II")

    def test_summary_formulas_point_at_the_right_columns(self):
        self.build([ranked_job("a", 74), ranked_job("u", 61, bucket="unsure")])
        ws = load_workbook(self.out)[xb.SUMMARY]
        values = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(2, 26)}
        self.assertIn("'Unsure Location'!$T$2", values["Status: Applied"])   # shifted by Location Check
        self.assertIn("'Top Matches'!$S$2", values["Status: Applied"])
        rate = values["Interview rate (interviews / applications)"]
        rows = {ws.cell(row=r, column=1).value: r for r in range(2, 26)}
        self.assertEqual(rate, f"=IFERROR(B{rows['Interviews scheduled']}/"
                               f"B{rows['Applications sent (has Date Applied)']},0)")


if __name__ == "__main__":
    unittest.main()
