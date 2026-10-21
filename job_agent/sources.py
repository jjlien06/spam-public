"""Other internship lists besides Simplify, parsed into queue rows.

speedyapply/2027-SWE-College-Jobs and 2027-AI-College-Jobs publish their USA
internships as Markdown tables (Company | Position | Location | Salary | Posting | Age)
under FAANG+ / Quant / Other headings.
"""
import hashlib
import re

from .queue import age_days, canonical, clean, platform

SPEEDYAPPLY = {
    "speedyapply-swe": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "speedyapply-ai": "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
}
SECTION = re.compile(r"^###\s+(FAANG\+|Quant|Other)(?=\s|$)")
CELL_TEXT = re.compile(r"<[^>]+>")


def parse_speedyapply(markdown, default_category):
    """Rows from the USA internship tables; Quant rows count as Quantitative Finance."""
    section, columns = "Other", []
    for line in markdown.splitlines():
        heading = SECTION.match(line)
        if heading:
            section = heading.group(1)
            continue
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        if cells[0] == "Company":  # the "Other" table has no Salary column
            columns = cells
            continue
        if len(cells) != len(columns):
            continue
        row = dict(zip(columns, cells))
        link = re.search(r'href="([^"]+)"[^>]*>\s*<img[^>]*alt="Apply"', row.get("Posting", ""))
        if not link:
            continue
        try:
            url = canonical(link.group(1))
        except ValueError:
            continue
        location = clean(CELL_TEXT.sub(" ", row["Location"]))
        yield {"id": hashlib.sha256(url.encode()).hexdigest()[:20],
               "company": clean(CELL_TEXT.sub(" ", row["Company"])),
               "role": clean(CELL_TEXT.sub(" ", row["Position"])),
               "location": location, "url": url, "platform": platform(url),
               "category": "Quantitative Finance" if section == "Quant" else default_category,
               "flags": "🔥" if section == "FAANG+" else "",
               "age_days": age_days(row.get("Age", "")),
               "locations": re.sub(r"\s*\+\d+$", "", location)}
