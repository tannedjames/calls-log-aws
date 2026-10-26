"""
Does the publisher's Parquet match the Glue tables? (ADR 9, Phase 2)

Athena reads a Parquet file through its Glue table: a column whose type
differs between the two fails every query on that partition. This builds a
made-up day the way the runner stages one (cloud/lake.py), turns it into the
publisher's three files (cloud/publisher.py) and the labels file
tools/label_calls.py writes, reads them back with pyarrow, and checks each
file against infra/lake.tf's columns and types, row for row.

Needs pyarrow at the AWS SDK for pandas layer's version (CI pins it); not the
OCR stack. Writes nothing. Exit code 0 = every table matches, 1 = a difference.
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # run as a script from tools/: the pipeline lives one level up

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from cloud import lake, naming, publisher  # noqa: E402
from parser import parse_row  # noqa: E402
from session_builder import build_sessions, dedupe  # noqa: E402

# How each Glue type reads back from the publisher's file.
ARROW = {"string": pa.string(), "date": pa.date32(), "int": pa.int32(), "double": pa.float64(),
         "boolean": pa.bool_(), "timestamp": pa.timestamp("ms")}
_UPLOAD = "20260601T083005Z-0a1b2c3d"
_ROWS = [   # fictional rows: (image, text, start line's confidence, duration line's)
    ("shot_01.png", "08:00AM 発信 20分0秒", 0.97, 0.93),
    ("shot_01.png", "08:21AM 発信 0秒", 0.96, 0.62),
    ("shot_01.png", "08:23AM 発信 37分0秒", 0.98, 0.95),
    ("shot_02.png", "09:30AM 不在着信", 0.96, None),
    ("shot_02.png", "10:00AM 発信 30分0秒", 0.97, 0.94),
]


def glue_columns(tf_text: str) -> dict:
    """{table: [(column, type), ...]} from infra/lake.tf's lake_columns."""
    block = re.search(r"lake_columns = \{(.*?)\n  \}", tf_text, re.S).group(1)
    return {table: re.findall(r'\["(\w+)", "(\w+)"\]', body)
            for table, body in re.findall(r"(\w+) = \[(.*?)\n    \]", block, re.S)}


def made_up_day() -> dict:
    records = []
    for image, text, start_conf, duration_conf in _ROWS:
        rec = parse_row(image, text, 0.95)
        rec.start_conf, rec.duration_conf = start_conf, duration_conf
        records.append(rec)
    kept, _dups = dedupe(records)
    sessions, _stats = build_sessions(kept)
    upload = {"image_count": 2, "attempts": 1, "ready_at": "2026-06-01T02:00:00Z",
              "started_at": "2026-06-01T02:01:00Z", "finished_at": "2026-06-01T02:03:00Z",
              "status": "Done", "runner_memory_mb": 2048}
    return lake.payload(naming.PersonDay("Nguyen Van A", "2026-06-01"), _UPLOAD,
                        image_names=["shot_01.png", "shot_02.png"], records=kept, sessions=sessions,
                        sheet=lake.day_sheet([(s.start_time, s.end_time) for s in sessions]),
                        upload=upload, salt=lake.LOCAL_SALT, staged_at="2026-06-01T02:03:01Z")


def made_up_labels(envelope: dict, ingested_at: _dt.datetime) -> list:
    """Labels for the made-up day's calls, as tools/label_calls.py imports them: the last batch
    finds two OCR misreads (a start, a duration) and one row that isn't a call; an older batch
    labelled the first call differently, and the views count only its latest label."""
    from tools import label_calls

    calls = envelope["calls"]
    snaps = {label_calls.ref(c): label_calls.snapshot(c) for c in calls}
    truth = {0: (calls[0]["start_ts"][11:16], str(calls[0]["duration_s"])),
             1: (calls[1]["start_ts"][11:16], "6"),                        # the OCR read 0 seconds
             2: ("08:24", str(calls[2]["duration_s"])),                    # the OCR read 08:23
             3: (calls[3]["start_ts"][11:16], "")}                         # a missed call: no duration
    filled = [{"ref": label_calls.ref(c), "true_start": truth[i][0] if i in truth else "",
               "true_duration": truth[i][1] if i in truth else "", "not_a_call": "" if i in truth else "x"}
              for i, c in enumerate(calls)]
    older = [{"ref": label_calls.ref(calls[0]), "true_start": "07:00", "true_duration": "", "not_a_call": ""}]
    rows = label_calls.label_rows(older, snaps, "labels-20260601T030000Z", "2026-06-01T03:00:00")[0]
    rows += label_calls.label_rows(filled, snaps, "labels-20260602T030000Z", "2026-06-02T03:00:00")[0]
    return publisher.typed_rows("labels", rows, ingested_at)


def main() -> int:
    envelope = made_up_day()
    problems = [f"the made-up day fails lake.check(): {p}" for p in lake.check(envelope)]
    glue = glue_columns((ROOT / "infra" / "lake.tf").read_text(encoding="utf-8"))
    if set(glue) != set(lake.COLUMN_TYPES):
        problems.append(f"infra/lake.tf has tables {sorted(glue)}, cloud/lake.py {sorted(lake.COLUMN_TYPES)}")
    rows = publisher.tables(envelope, _dt.datetime(2026, 6, 1, 2, 3, 2))
    rows["labels"] = made_up_labels(envelope, _dt.datetime(2026, 6, 1, 2, 3, 2))
    for table, columns in glue.items():
        # One thread: pyarrow's reader pool can hang the interpreter's exit
        # (seen with 24.0.0). The publisher only writes, so Lambda is unaffected.
        back = pq.read_table(io.BytesIO(publisher.parquet_bytes(table, rows.get(table, []))), use_threads=False)
        want = pa.schema([(name, ARROW[kind]) for name, kind in columns])
        if not back.schema.equals(want):
            problems.append(f"{table}: the file's schema\n{back.schema}\n  differs from Glue's\n{want}")
        elif back.to_pylist() != rows[table]:
            problems.append(f"{table}: the rows don't read back as written")
        print(f"{table}: {back.num_rows} rows, {len(columns)} columns -> "
              + ("OK" if not any(p.startswith(f"{table}:") for p in problems) else "DIFFERENT"))
    for line in problems:
        print(f"    {line}")
    print("Lake Parquet: " + ("matches the Glue tables." if not problems else "DIFFERENT."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
