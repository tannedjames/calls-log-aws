"""
The data bucket (AD-8): uploaded screenshots in inbox/<uploadId>/, per-image
checkpoints in work/<uploadId>/ and Day Sheets in results/<Person-Day>.xlsx.
Objects are never moved or deleted to signal state (AD-4); lifecycle rules
expire inbox/ and work/ (infra/data.tf).

Uses the S3 client passed in (boto3.client("s3")), so this module imports no
AWS library and tests can fake the bucket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cloud import naming

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")   # main.IMAGE_SUFFIXES


def pc_order(names: List[str]) -> List[str]:
    """
    The order main.gather_jpg_files gives on the Owner's Windows PC, where
    sorted() over Paths compares names case-insensitively. dedupe keeps the
    first of two duplicates, so the order must match the PC's (AD-2).
    """
    return sorted(names, key=lambda n: (n.lower(), n))


class S3Storage:
    def __init__(self, client: Any, bucket: str):
        self.client, self.bucket = client, bucket

    def list_images(self, upload_id: str) -> List[str]:
        """File names of the images uploaded under inbox/<uploadId>/, in the PC's order."""
        prefix = naming.inbox_key(upload_id, "")
        names: List[str] = []
        kwargs: Dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if "/" not in name and name.lower().endswith(IMAGE_SUFFIXES):
                    names.append(name)
            if not page.get("IsTruncated"):
                break
            kwargs["ContinuationToken"] = page["NextContinuationToken"]
        return pc_order(names)

    def download(self, upload_id: str, name: str, folder: Path) -> Path:
        target = folder / name
        self.client.download_file(self.bucket, naming.inbox_key(upload_id, name), str(target))
        return target

    def put_result(self, day: naming.PersonDay, path: Path) -> Optional[str]:
        """Store a Day Sheet as a new version of results/<Person-Day>.xlsx; returns the version id."""
        with path.open("rb") as body:
            out = self.client.put_object(
                Bucket=self.bucket, Key=naming.result_key(day), Body=body,
                ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return out.get("VersionId")

    # ----- per-image checkpoints (AD-16) -------------------------------------

    def get_checkpoint(self, upload_id: str, name: str) -> Optional[list]:
        try:
            out = self.client.get_object(Bucket=self.bucket, Key=naming.work_key(upload_id, name))
        except Exception as e:
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return json.loads(out["Body"].read().decode("utf-8"))

    def put_checkpoint(self, upload_id: str, name: str, records: list) -> None:
        self.client.put_object(Bucket=self.bucket, Key=naming.work_key(upload_id, name),
                               Body=json.dumps(records, ensure_ascii=False).encode("utf-8"),
                               ContentType="application/json")
