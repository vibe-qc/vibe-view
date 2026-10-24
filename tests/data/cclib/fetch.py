"""Explicitly download the pinned optional regression corpus to a chosen directory."""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
for record in json.loads(Path(__file__).with_name("references.json").read_text()):
    url = ("https://raw.githubusercontent.com/cclib/cclib/"
           + record["revision"] + "/" + record["source"])
    payload = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ValueError("Regression corpus checksum mismatch")
    (root / record["file"]).write_bytes(payload)
