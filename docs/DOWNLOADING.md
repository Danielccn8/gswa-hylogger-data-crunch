# Downloading guide

## Processed logs

Use `processed preview` before fetching. A preview stores an inventory but does not download the scalar series.

```powershell
hylogger processed preview 05GJD001 --contains "Min"
hylogger processed fetch 05GJD001 --profile clustering
```

Profiles are transparent glob lists in `configs/project.toml`. Use repeated `--log` options for exact products or `--all-logs` for a complete scalar snapshot.

## Raw packages

Raw packages can be hundreds of megabytes or several gigabytes. Probe first:

```powershell
hylogger raw probe 05GJD001
hylogger raw fetch 05GJD001 --components spectra --dry-run
```

The downloader resolves the official URL, checks size and free space, writes a `.part` file, resumes through HTTP Range, verifies byte count, atomically finalises the ZIP, calculates SHA-256, safely extracts requested components, and verifies spectral extraction with `pytsg`.

The server distributes one ZIP per hole. Selecting `spectra` reduces extracted disk usage, not network transfer size.

## Recovery

Run the same command after a timeout or interruption. Do not rename or delete `.part` files unless intentionally abandoning a download. Use `hylogger status` to inspect failed and completed artifacts.

## Existing packages

Reference mode avoids a second large copy:

```powershell
hylogger raw import "D:\ExistingData\05GJD001.zip" --mode reference --components spectra
```

Reference mode records the absolute path. If the external file is moved, import it again.
