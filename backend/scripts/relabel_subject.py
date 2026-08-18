"""Relabel an already-ingested subject, without re-embedding anything.

    python scripts/relabel_subject.py --grade 5 --from Science --to EVS

Why this exists rather than "just re-ingest": the subject lives only on
`curriculum_documents.subject`. It is not part of the embedding, not part of the
contextual chunk header, and not part of `chunk_tsv`. So a mislabelled class is a
one-column UPDATE, not a re-embedding run — re-ingesting would spend money and
time recomputing 1536-dim vectors that are already correct.

The label is not cosmetic. `/v1/curriculum/subjects` surfaces it verbatim in the
class picker, and `search_chunks(filter_subject := ...)` filters on it, so a wrong
label both tells a student the wrong book and makes a subject-scoped chat miss the
chapters it should find.

Dry-run by default; pass --apply to write.
"""
import argparse

import _bootstrap  # noqa: F401  isort:skip

from sqlalchemy import text

from app.db.session import SessionLocal


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grade", type=int, required=True)
    ap.add_argument("--from", dest="old", required=True, help="current (wrong) subject label")
    ap.add_argument("--to", dest="new", required=True, help="correct subject label")
    ap.add_argument("--apply", action="store_true", help="write the change (default is a dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT d.id, d.chapter_no, d.title, count(c.id) AS chunks "
                "FROM curriculum_documents d "
                "LEFT JOIN curriculum_chunks c ON c.document_id = d.id "
                "WHERE d.grade = :g AND d.subject = :old "
                "GROUP BY d.id, d.chapter_no, d.title ORDER BY d.chapter_no"
            ),
            {"g": args.grade, "old": args.old},
        ).all()

        if not rows:
            print(f"Nothing to do: no Class {args.grade} documents labelled {args.old!r}.")
            # Show what IS there, so a typo in --from is obvious rather than silent.
            have = db.execute(
                text(
                    "SELECT subject, count(*) FROM curriculum_documents "
                    "WHERE grade = :g GROUP BY subject"
                ),
                {"g": args.grade},
            ).all()
            for subject, n in have:
                print(f"  Class {args.grade} currently has {n} document(s) labelled {subject!r}.")
            return

        print(f"Class {args.grade}: {len(rows)} document(s) labelled {args.old!r} -> {args.new!r}")
        for _id, ch, title, chunks in rows:
            print(f"  ch.{ch:>2}  {title}  ({chunks} chunks, embeddings untouched)")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        result = db.execute(
            text(
                "UPDATE curriculum_documents SET subject = :new "
                "WHERE grade = :g AND subject = :old"
            ),
            {"g": args.grade, "old": args.old, "new": args.new},
        )
        db.commit()
        print(f"\nUpdated {result.rowcount} document(s). No embeddings were recomputed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
