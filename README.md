# CadenceLibHub

A Windows Python GUI and CLI for managing Cadence Capture and Allegro libraries, with LCSC and SamacSys Library Loader workflows.

## Quick start

1. Install Python 3.10 or newer and Git.
2. Place or clone this repository as `MyLib/Tools`.
3. Install dependencies from this folder:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Copy `config.example.json` to `config.json` and update the Library Loader and Downloads paths if needed.
5. Double-click `CadenceLibHub.cmd` to open the GUI.

The default library root is the parent of the `Tools` folder. The application creates and uses `Symbols`, `Footprints`, `3D`, `Sources`, `_staging`, `Scripts`, and `manifest.csv` under that root.

## Features

- Search LCSC parts using the LCSC/EasyEDA product API.
- Export LCSC component data and generate Capture Tcl and Allegro SKILL helpers.
- Search with SamacSys Library Loader and synchronize downloaded library files.
- Preview LCSC component and footprint images in the GUI.
- Check symbol, footprint, and 3D model inventory.
- Use the command line for search, add, promote, sync, check, and list operations.

## Command line

Run commands from this folder:

```powershell
.\lib.cmd search "type-c 16p" --limit 20
.\lib.cmd search "STM32F103" --source samacsys
.\lib.cmd add C165948
.\lib.cmd promote C165948 --clean
.\lib.cmd sync
.\lib.cmd check
.\lib.cmd list
```

LCSC export requires `npnp.exe` at `bin/npnp.exe`, or a custom path set in `config.json`. The binary is not included in this repository. It is distributed under the Non-Commercial Share-Alike License 1.0. See `npnp_LICENSE.txt` and the original project at https://github.com/ref42/npnp. Obtain it from its original project and observe its license terms.

SamacSys search requires a browser login and the Library Loader application. Capture XML import and Allegro `.il` execution remain manual, version-sensitive steps.

## Configuration

`config.json` is local and ignored by Git. `config.example.json` documents optional path overrides. Relative paths resolve from the `Tools` folder. If the project is stored as `MyLib/Tools`, the default paths use the containing `MyLib` folder.

The repository intentionally excludes local library data, personal paths, preview caches, generated files, and the separately licensed `npnp.exe` binary.
