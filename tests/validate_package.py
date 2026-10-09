import os
import re
import struct
import sys
import zipfile
from pathlib import Path


package = Path(sys.argv[1])
required = {
	"LICENSE",
	"deps.txt",
	"manifest.ini",
	"synthDrivers/bestspeech.py",
	"synthDrivers/b32_helper.exe",
	"synthDrivers/b32_tts.dll",
	"synthDrivers/b32_wrapper.dll",
}
with zipfile.ZipFile(package) as archive:
	names = {name.replace("\\", "/") for name in archive.namelist() if not name.endswith("/")}
	missing = required - names
	assert not missing, f"package is missing: {sorted(missing)}"
	assert not any(name.startswith("bin/") for name in names), "native files must be beside the synth driver"
	assert not any("__pycache__" in name or name.endswith(".pyc") for name in names), "package contains Python cache files"
	for binary in required & {name for name in required if name.endswith((".dll", ".exe"))}:
		image = archive.read(binary)
		assert image[:2] == b"MZ", f"{binary} is not a Windows binary"
		peOffset = struct.unpack_from("<I", image, 0x3C)[0]
		assert image[peOffset:peOffset + 4] == b"PE\0\0", f"{binary} has no PE header"
		machine = struct.unpack_from("<H", image, peOffset + 4)[0]
		assert machine == 0x14C, f"{binary} must be 32-bit x86, found PE machine 0x{machine:04X}"
	manifest = archive.read("manifest.ini").decode("utf-8-sig")
	dependency_notices = archive.read("deps.txt").decode("utf-8-sig")
	assert "Sonic" in dependency_notices and "Apache License" in dependency_notices


def field(name):
	match = re.search(rf"(?m)^{re.escape(name)}\s*=\s*\"?([^\"\r\n]+)", manifest)
	assert match, f"manifest has no {name}"
	return match.group(1).strip()


version = field("version")
assert re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version), f"invalid add-on version: {version}"
for name in ("minimumNVDAVersion", "lastTestedNVDAVersion"):
	assert re.fullmatch(r"\d{4}\.\d+(?:\.\d+)?", field(name)), f"invalid {name}"
if os.environ.get("GITHUB_REF_TYPE") == "tag":
	tag = os.environ["GITHUB_REF_NAME"].removeprefix("v")
	assert tag == version, f"release tag {tag} does not match manifest version {version}"
print(f"Package validation: OK ({version})")
