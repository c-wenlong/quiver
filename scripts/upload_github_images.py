#!/usr/bin/env python3
"""Upload quiver repo images to GitHub (avatar + social preview).

GitHub has no public REST API for repository profile/social images. This script
uses GitHub's internal repository-images upload endpoint with a token from
``gh auth token``.
"""

import json
import mimetypes
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "c-wenlong/quiver"
UPLOAD_URL = f"https://github.com/{REPO}/upload/repository-images"


def _token() -> str:
    result = subprocess.run(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _multipart_body(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----quiverUploadBoundary7MA4YWxkTrZu0gW"
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.extend(
            [
                f"--{boundary}".encode(),
                f'Content-Disposition: form-data; name="{name}"'.encode(),
                b"",
                value.encode(),
            ]
        )
    for name, (filename, content, mime) in files.items():
        lines.extend(
            [
                f"--{boundary}".encode(),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode(),
                f"Content-Type: {mime}".encode(),
                b"",
                content,
            ]
        )
    lines.extend([f"--{boundary}--".encode(), b""])
    body = b"\r\n".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def upload_image(path: Path, *, purpose: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body, content_type = _multipart_body(
        {"purpose": purpose},
        {"file": (path.name, path.read_bytes(), mime)},
    )
    request = urllib.request.Request(
        UPLOAD_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "quiver-upload-script",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    mascot = root / "assets" / "mascot.png"
    social = root / "assets" / "social-preview.png"

    targets = argv or ["avatar", "social"]
    for target in targets:
        if target == "avatar":
            path, purpose = mascot, "avatar"
        elif target in ("social", "social-preview"):
            path, purpose = social, "social_preview"
        else:
            print(f"Unknown target: {target}", file=sys.stderr)
            return 1
        try:
            result = upload_image(path, purpose=purpose)
            print(f"Uploaded {target}: {json.dumps(result, indent=2)}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            print(f"Upload failed for {target} ({exc.code}): {body}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Upload failed for {target}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
