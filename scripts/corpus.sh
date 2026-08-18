#!/usr/bin/env bash
#
# Export the ingested NCERT corpus to a portable dump, or load one back.
#
#   scripts/corpus.sh export [file]     write the corpus to a .dump
#   scripts/corpus.sh import <file>     load a .dump into this stack
#   scripts/corpus.sh status            what is currently ingested
#
# WHY THIS EXISTS
# Ingestion needs ~2 GB for the api container (docker-compose.ingest.yml) and
# costs real money in embedding calls. Doing it once on a laptop and shipping
# the result means the server never needs that headroom, embeddings are never
# recomputed, and a rebuilt server is minutes away instead of a re-ingest.
#
# Only curriculum_documents and curriculum_chunks are touched. A full pg_dump
# would carry local test students, conversations and moderation_flags along
# with it — junk at best, children's test data at worst.
#
# The embedding vectors survive the round trip unchanged, so a restored corpus
# retrieves identically to the one it was exported from. chunk_tsv is a
# GENERATED column: pg_dump excludes it and Postgres rebuilds it on insert.
# The id sequence travels with the dump, so ingesting more chapters after a
# restore does not collide.

set -euo pipefail

DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${POSTGRES_USER:-guruji}"
DB_NAME="${POSTGRES_DB:-guruji}"
TABLES=(curriculum_documents curriculum_chunks)

die() { echo "error: $*" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  else docker-compose "$@"; fi
}


db_exec() {
  local tool="$1"; shift
  compose exec -T "$DB_SERVICE" \
    sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec "$@"' _ "$tool" -U "$DB_USER" -d "$DB_NAME" "$@"
}

require_db() {
  compose ps --status running --services 2>/dev/null | grep -qx "$DB_SERVICE" \
    || die "the '$DB_SERVICE' service is not running. Start the stack first."
}

status() {
  require_db
  db_exec psql -q -c "
    SELECT d.grade,
           d.subject,
           count(DISTINCT d.id) AS chapters,
           count(c.id)          AS chunks
      FROM curriculum_documents d
      LEFT JOIN curriculum_chunks c ON c.document_id = d.id
     GROUP BY d.grade, d.subject
     ORDER BY d.grade, d.subject;"
}

do_export() {
  local out="${1:-corpus-$(date +%Y-%m-%d).dump}"
  require_db

  local chunks
  chunks=$(db_exec psql -tA -c "SELECT count(*) FROM curriculum_chunks" | tr -d '\r')
  [ "$chunks" -gt 0 ] || die "the corpus is empty — nothing to export. Ingest first."

  local args=()
  for t in "${TABLES[@]}"; do args+=(--table="$t"); done

  # -Fc  custom format, so pg_restore can be selective on the way back in
  # -Z9  the vectors are float text and compress ~2.6x
  # --data-only  the target's own init_db() owns the schema; shipping DDL from a
  #              laptop would let a stale local schema overwrite the server's
  db_exec pg_dump --data-only --no-owner -Fc -Z9 "${args[@]}" > "$out"

  echo "exported $chunks chunks -> $out ($(du -h "$out" | cut -f1))"
  echo "transfer with:  scp $out <user>@<host>:~/"
}

do_import() {
  local in="${1:?usage: corpus.sh import <file.dump>}"
  [ -f "$in" ] || die "no such file: $in"
  require_db

  local existing
  existing=$(db_exec psql -tA -c "SELECT count(*) FROM curriculum_chunks" | tr -d '\r')

  # --data-only APPENDS. Importing over a populated corpus silently doubles
  # every chapter, and doubled chunks skew retrieval rather than erroring.
  if [ "$existing" -gt 0 ]; then
    echo "WARNING: this database already holds $existing chunks."
    echo "Importing would ADD to them, not replace them, and duplicate chapters"
    echo "distort retrieval without producing an error."
    read -r -p "Truncate the existing corpus first? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "aborted; nothing changed."
    db_exec psql -q -c "TRUNCATE curriculum_chunks, curriculum_documents RESTART IDENTITY CASCADE;"
    echo "existing corpus cleared."
  fi

  db_exec pg_restore --data-only --no-owner < "$in"

  echo "imported. current corpus:"
  status
}

case "${1:-}" in
  export) shift; do_export "${1:-}" ;;
  import) shift; do_import "${1:-}" ;;
  status) status ;;
  *) die "usage: $(basename "$0") {export [file] | import <file> | status}" ;;
esac