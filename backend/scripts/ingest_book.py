"""Batch-ingest an entire book by looping ingest_curriculum.py over a manifest.

Does NOT replace ingest_curriculum.py or duplicate its chunking/embedding logic
— that script stays the single source of truth for how a chapter actually gets
ingested. This is a thin wrapper that calls it once per chapter as a
subprocess, exactly as if you'd typed each command by hand. One subject and
one grade per run; per-chapter detail (PDF path, chapter number, title) comes
from a CSV manifest.

Usage:
  python ingest_book.py manifest.csv --subject Science --grade 8
  python ingest_book.py manifest.csv --subject Science --grade 8 --dry-run
  python ingest_book.py manifest.csv --subject Science --grade 8 --force

Manifest format (CSV, header row required):
  chapter_no,pdf_path,title
  1,pdfs/ch01.pdf,Crop Production and Management
  2,pdfs/ch02.pdf,"Microorganisms: Friend, and Foe"

Quote a title only if it contains a comma — ordinary CSV rules, nothing
project-specific.
"""
import argparse
import csv
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

# Puts backend/ on sys.path; must come before any `app.*` import.
import _bootstrap  # noqa: F401  isort:skip

from app.db.session import SessionLocal, init_db

# Resolved from this file's own location, not from the working directory. The
# manifest's `pdf_path` values stay relative to the CWD (they point outside the
# repo, at Books/), but the sibling script must not — otherwise this only works
# when invoked from exactly one directory.
INGEST_SCRIPT = Path(__file__).resolve().parent / "ingest_curriculum.py"

# The subject names retrieval filters on. Nothing in ingest_curriculum.py itself
# checks this — a typo here ingests cleanly, with no error, and is then silently
# unretrievable forever. One typo in an 18-chapter batch would otherwise cost 18
# embedding runs to discover instead of zero, which is the entire reason this check
# exists in the wrapper even though it doesn't exist in the tool it's wrapping.
#
# EVS is here because Class 5 is not Science: the NCERT Class 5 book is the
# integrated environmental-studies text "Our Wondrous World". Leaving EVS out of
# this set does not prevent the mislabel — it forces it, because the only value the
# wrapper then accepts for Class 5 is a wrong one. The subject label is surfaced
# verbatim to students by /v1/curriculum/subjects, so a wrong label is a wrong
# promise about which book an answer came from.
CANONICAL_SUBJECTS = {"Mathematics", "Science", "Social Science", "English", "Hindi", "EVS"}


def load_manifest(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"chapter_no", "pdf_path", "title"} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"Manifest is missing required column(s): {', '.join(sorted(missing))}")

        rows: list[dict] = []
        for line_no, row in enumerate(reader, start=2):  # header is line 1
            try:
                chapter_no = int(row["chapter_no"])
            except ValueError:
                sys.exit(f"Line {line_no}: chapter_no {row['chapter_no']!r} is not an integer.")
            pdf_path = row["pdf_path"].strip()
            title = row["title"].strip()
            if not pdf_path or not title:
                sys.exit(f"Line {line_no}: pdf_path and title cannot be empty.")
            rows.append({"chapter_no": chapter_no, "pdf_path": pdf_path, "title": title})

    if not rows:
        sys.exit("Manifest has a header but no data rows.")

    seen: set[int] = set()
    for row in rows:
        if row["chapter_no"] in seen:
            sys.exit(
                f"Manifest lists chapter_no {row['chapter_no']} more than once — "
                f"fix the CSV before running anything."
            )
        seen.add(row["chapter_no"])

    return rows


def already_ingested(db, subject: str, grade: int, chapter_no: int) -> bool:
    result = db.execute(
        text(
            "SELECT 1 FROM curriculum_documents "
            "WHERE subject = :subject AND grade = :grade AND chapter_no = :chapter_no "
            "LIMIT 1"
        ),
        {"subject": subject, "grade": grade, "chapter_no": chapter_no},
    )
    return result.first() is not None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--subject", required=True,
        help="Exact canonical name: Mathematics, Science, Social Science, English, or Hindi.",
    )
    parser.add_argument("--grade", type=int, required=True)
    parser.add_argument(
        "--force", action="store_true",
        help="Re-ingest chapters that already exist instead of skipping them "
             "(creates a duplicate document + chunk set — see the README note).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate the manifest and confirm every PDF path exists, then exit. "
             "Touches neither the database nor the OpenAI API — nothing is spent.",
    )
    args = parser.parse_args()

    if args.subject not in CANONICAL_SUBJECTS:
        sys.exit(
            f"--subject {args.subject!r} is not one of the five names retrieval filters on: "
            f"{', '.join(sorted(CANONICAL_SUBJECTS))}. Fixing this now is free; fixing it after "
            f"an 18-chapter ingest run is not."
        )

    rows = load_manifest(args.manifest)
    print(f"Manifest OK: {len(rows)} chapter(s) for {args.subject} Class {args.grade}.")

    missing_pdfs = [r["pdf_path"] for r in rows if not Path(r["pdf_path"]).is_file()]
    if missing_pdfs:
        sys.exit("Missing PDF file(s) — fix the manifest before running anything:\n  " + "\n  ".join(missing_pdfs))

    if args.dry_run:
        print("Dry run only — manifest and every PDF path check out. Nothing ingested, nothing spent.")
        return

    # One shared session for every skip-check in the batch, not one per row —
    # cheap, and it's the only thing this wrapper touches the database for
    # directly. The actual ingestion still goes through ingest_curriculum.py's
    # own, separate database session per subprocess call.
    init_db()
    db = SessionLocal()
    try:
        to_skip = [r for r in rows if not args.force and already_ingested(db, args.subject, args.grade, r["chapter_no"])]
        to_run = [r for r in rows if r not in to_skip]
    finally:
        db.close()

    for row in to_skip:
        print(f"SKIP   ch.{row['chapter_no']:>2}  {row['title']}  (already ingested — rerun with --force to replace)")

    succeeded: list[dict] = []
    failed: list[dict] = []

    for row in to_run:
        print(f"RUN    ch.{row['chapter_no']:>2}  {row['title']} ...", flush=True)
        # sys.executable, not a bare "python" string — guarantees the subprocess
        # runs under the exact same interpreter/venv as this wrapper, not
        # whatever "python" happens to resolve to on the shell's PATH.
        # A real argv list, not a shell string — no quoting to get wrong, no
        # shell injection surface even from an adversarial title.
        result = subprocess.run(
            [
                sys.executable, str(INGEST_SCRIPT), row["pdf_path"],
                "--subject", args.subject,
                "--grade", str(args.grade),
                "--chapter-no", str(row["chapter_no"]),
                "--title", row["title"],
            ],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            succeeded.append(row)
            last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "done"
            print(f"  ok — {last_line}")
        else:
            failed.append(row)
            print(f"  FAILED (exit {result.returncode})")
            for line in (result.stderr or result.stdout).strip().splitlines():
                print(f"    {line}")
            # Deliberately does NOT stop the batch. One bad PDF path on chapter
            # 9 of 18 shouldn't throw away chapters 1-8's already-paid-for
            # embedding work, and shouldn't block 10-18 from running either.

    print()
    print(f"Done: {len(succeeded)} ingested, {len(to_skip)} skipped, {len(failed)} failed.")
    if failed:
        print("Failed chapters — fix and re-run the SAME manifest; already-ingested")
        print("chapters (including any that just succeeded above) will be skipped automatically:")
        for row in failed:
            print(f"  ch.{row['chapter_no']} — {row['pdf_path']}")
        sys.exit(1)


if __name__ == "__main__":
    main()