import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


source = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
excluded_directories = {"__pycache__", "outputs"}
excluded_files = {"build.ps1", "mock_b32_tts.dll"}

destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
	dir=destination.parent,
	prefix=f"{destination.name}.",
	suffix=".tmp",
	delete=False,
) as temporary:
	temporary_path = Path(temporary.name)

try:
	with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		for path in sorted(source.rglob("*")):
			relative = path.relative_to(source)
			if path.is_dir() or any(part in excluded_directories for part in relative.parts):
				continue
			if path.name in excluded_files or path.suffix.casefold() in {".pyc", ".pyo"}:
				continue
			archive.write(path, relative.as_posix())
	shutil.move(temporary_path, destination)
finally:
	temporary_path.unlink(missing_ok=True)

print(destination)
