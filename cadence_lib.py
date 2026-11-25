#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CadenceLibHub
-------------
One entry point for LCSC (via npnp) and SamacSys Library Loader.

Layout:
  <library-root>/
    Symbols/       .olb
    Footprints/    .dra/.psm/.pad
    3D/            .step/.stp
    Sources/       source XML/IL/zip archives
    _staging/      temporary exports
    Scripts/       generated Capture Tcl / Allegro IL helpers
    manifest.csv

Usage:
  python cadence_lib.py search "type-c 16p" --source lcsc
  python cadence_lib.py search "STM32F103" --source samacsys
  python cadence_lib.py add C165948
  python cadence_lib.py promote C165948 --clean
  python cadence_lib.py sync
  python cadence_lib.py check
  python cadence_lib.py list
  python cadence_lib.py gui
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from pathlib import Path

try:
    import olefile  # type: ignore
except Exception:  # pragma: no cover
    olefile = None

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LIBRARY_ROOT = HERE.parent
DEFAULT_CONFIG = {
    "root": str(LIBRARY_ROOT),
    "symbols": str(LIBRARY_ROOT / "Symbols"),
    "footprints": str(LIBRARY_ROOT / "Footprints"),
    "models3d": str(LIBRARY_ROOT / "3D"),
    "sources": str(LIBRARY_ROOT / "Sources"),
    "staging": str(LIBRARY_ROOT / "_staging"),
    "scripts": str(LIBRARY_ROOT / "Scripts"),
    "manifest": str(LIBRARY_ROOT / "manifest.csv"),
    "npnp": str(HERE / "bin" / "npnp.exe"),
    "library_loader": str(Path.home() / "AppData/Local/Programs/SamacSys/Library Loader/Library Loader.exe"),
    "downloads": str(Path.home() / "Downloads"),
    "samacsys_search": "https://componentsearchengine.com/search?term={query}",
    "lcsc_search_url": "https://www.lcsc.com/search?q={query}",
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
        except Exception as exc:
            print(f"[warn] cannot read {CONFIG_PATH}: {exc}")
    return dict(DEFAULT_CONFIG)


CFG = load_config()


def P(key: str) -> Path:
    path = Path(CFG[key]).expanduser()
    return path if path.is_absolute() else HERE / path


def pstr(key: str) -> str:
    return P(key).as_posix()


ROOT = P("root")
SYMBOLS = P("symbols")
FOOTPRINTS = P("footprints")
MODELS = P("models3d")
SOURCES = P("sources")
STAGING = P("staging")
SCRIPTS = P("scripts")
MANIFEST = P("manifest")
NPNP = P("npnp")
DOWNLOADS = P("downloads")
LIBRARY_LOADER = P("library_loader")

MANIFEST_FIELDS = ["Source", "LCSC_ID", "MPN", "Symbol", "Footprint", "3D", "Date"]

PROGRESS_CB = None


def set_progress_callback(cb):
    """GUI hook: cb(text, state) with state in running/done/error."""
    global PROGRESS_CB
    PROGRESS_CB = cb


def progress_step(text: str, state: str = "running") -> None:
    cb = PROGRESS_CB
    if cb is None:
        return
    try:
        cb(text, state)
    except Exception:
        pass


def ensure_dirs() -> None:
    for p in (SYMBOLS, FOOTPRINTS, MODELS, SOURCES, STAGING, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], cwd: Path | None = None) -> int:
    print("+ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def run_capture(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(NPNP)] + args,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def read_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def write_manifest(rows: list[dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})


def upsert_manifest(row: dict) -> None:
    rows = read_manifest()
    keys = [r for r in rows if r.get("LCSC_ID") == row.get("LCSC_ID") and r.get("LCSC_ID")]
    if not keys:
        keys = [r for r in rows if r.get("Symbol") == row.get("Symbol") and r.get("Symbol")]
    if keys:
        keys[0].update(row)
    else:
        rows.append(row)
    write_manifest(rows)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
LCSC_SEARCH_API = "https://pro.lceda.cn/api/szlcsc/eda/product/list"
LCSC_SVG_API = "https://easyeda.com/api/products/{component}/svgs"
LCSC_PRODUCT_API = "https://easyeda.com/api/products/{component}/components"


def _image_url_from(raw: dict) -> str:
    """Normalize the image fields returned by LCSC product search."""
    candidates = [raw.get("images"), raw.get("image"), raw.get("image_url")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            for key in ("url", "src", "middle", "large", "original"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(candidate, list):
            for image in candidate:
                if isinstance(image, str) and image.strip():
                    return image.strip()
                if isinstance(image, dict):
                    for key in ("url", "src", "middle", "large", "original"):
                        value = image.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
    return ""


def lcsc_search(keyword: str, limit: int = 20) -> tuple[int, str, list[dict]]:
    """Search the LCSC/EasyEDA product list API and return normalized rows."""
    ensure_dirs()
    progress_step("查询 LCSC 搜索接口")
    url = LCSC_SEARCH_API + "?wd=" + urllib.parse.quote(keyword)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CadenceLibHub/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw_items = payload.get("result") or []
        progress_step("查询 LCSC 搜索接口", "done")
    except Exception as exc:
        progress_step("LCSC 接口失败，尝试 npnp 回退", "error")
        # Fall back to npnp's console output. On some Windows consoles its
        # output bypasses pipes, so this is only a best-effort fallback.
        code, out = run_capture(["search", keyword, "--limit", str(limit)])
        results = []
        pattern = re.compile(
            r'^\s*\[\s*(\d+)\]\s*(.*?)\s*\|\s*LCSC ID:\s*(C\d+)\s*\|\s*Manufacturer:\s*(.*?)\s*\|\s*3D model:\s*(\w+)',
            re.I,
        )
        for line in out.splitlines():
            m = pattern.match(line)
            if m:
                results.append(
                    {
                        "idx": m.group(1),
                        "mpn": m.group(2).strip(),
                        "lcsc": m.group(3),
                        "mfr": m.group(4).strip(),
                        "model": m.group(5).strip(),
                        "desc": "",
                    }
                )
        return code, f"{exc}\n{out}", results

    results = []
    for i, raw in enumerate(raw_items[:limit], 1):
        attrs = raw.get("attributes") or {}
        image_url = _image_url_from(raw)
        footprint = raw.get("footprint") or {}
        lcsc = raw.get("product_code") or attrs.get("Supplier Part") or ""
        mpn = (
            raw.get("display_title")
            or attrs.get("Manufacturer Part")
            or raw.get("title")
            or ""
        )
        mfr = attrs.get("Manufacturer") or ""
        model = "yes" if attrs.get("3D Model") else "no"
        desc = raw.get("description") or attrs.get("LCSC Part Name") or ""
        results.append(
            {
                "idx": i,
                "mpn": mpn,
                "lcsc": lcsc,
                "mfr": mfr,
                "model": model,
                "desc": desc,
                "image_url": image_url,
                "footprint_name": (
                    footprint.get("display_title")
                    or attrs.get("Supplier Footprint")
                    or ""
                ),
            }
        )
    progress_step(f"找到 {len(results)} 个结果", "done")
    return 0, f"LCSC API: {len(raw_items)} result(s), showing {len(results)}", results


def lcsc_footprint_svg(component: str) -> bytes:
    """Fetch the PCB footprint SVG associated with an LCSC component code."""
    component = component.strip().upper()
    if not re.fullmatch(r"C\d+", component):
        raise ValueError(f"Invalid LCSC component code: {component}")

    page_url = f"https://www.szlcsc.com/search?wd={urllib.parse.quote(component)}"
    req = urllib.request.Request(
        LCSC_SVG_API.format(component=urllib.parse.quote(component)),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Origin": "https://www.lcsc.com",
            "Referer": page_url,
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body)

    if not (payload.get("success") or payload.get("code") == 200):
        raise RuntimeError("LCSC footprint data is unavailable")

    items = payload.get("result") or payload.get("data") or []
    for item in items:
        if str(item.get("docType", "")).lower() in {"4", "footprint", "pcb"}:
            svg = item.get("svg")
            if isinstance(svg, str) and "<svg" in svg.lower():
                return svg.encode("utf-8")
    raise RuntimeError("LCSC did not return a PCB footprint SVG")


def lcsc_product_url(component: str) -> str:
    """Resolve an LCSC number to its domestic LCSC product detail page."""
    component = component.strip().upper()
    if not re.fullmatch(r"C\d+", component):
        raise ValueError(f"Invalid LCSC component code: {component}")
    req = urllib.request.Request(
        LCSC_PRODUCT_API.format(component=urllib.parse.quote(component)),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    product = payload.get("result") or {}
    product_id = (product.get("szlcsc") or {}).get("id")
    if not product_id:
        raise RuntimeError(f"Domestic LCSC product page is unavailable for {component}")
    return f"https://www.szlcsc.com/product/details_{product_id}.html"


def search_lcsc(keyword: str, limit: int = 20) -> None:
    code, out, results = lcsc_search(keyword, limit)
    if results:
        print(f"{'#':>3}  {'LCSC':10}  {'MPN':34}  {'Manufacturer':24}  3D")
        print("-" * 90)
        for r in results:
            print(
                f"{r['idx']:>3}  {r['lcsc']:10}  {r['mpn'][:34]:34}  "
                f"{r['mfr'][:24]:24}  {r['model']}"
            )
    else:
        print(out.rstrip())
    if code != 0:
        print(f"[error] npnp search exited with {code}", file=sys.stderr)


def search_samacsys(keyword: str) -> None:
    progress_step("打开 SamacSys 搜索页面")
    url = CFG["samacsys_search"].format(query=keyword.replace(" ", "+"))
    print(f"SamacSys search: {url}")
    print("如果 Library Loader 没启动，先启动它；登录后下载的模型会进 Downloads。")
    webbrowser.open(url)
    if LIBRARY_LOADER.exists():
        try:
            subprocess.Popen([str(LIBRARY_LOADER)])
            progress_step("启动 Library Loader", "done")
        except Exception as exc:
            progress_step("启动 Library Loader 失败", "error")
            print(f"[warn] cannot start Library Loader: {exc}")
    else:
        progress_step("未找到 Library Loader", "error")


def cmd_search(args: argparse.Namespace) -> int:
    if args.source in ("lcsc", "all"):
        search_lcsc(args.keyword, args.limit)
    if args.source in ("samacsys", "all"):
        search_samacsys(args.keyword)
    return 0


# ---------------------------------------------------------------------------
# LCSC export / promote
# ---------------------------------------------------------------------------
def parse_xml_meta(xml_path: Path) -> tuple[str, str, str]:
    """Return (part_name, footprint, mpn)."""
    try:
        text = xml_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", "", ""
    m = re.search(r'<Defn[^>]*name="([^"]+)"[^>]*pcbFootprint="([^"]+)"', text)
    part = m.group(1) if m else ""
    fp = m.group(2) if m else ""
    mpn = ""
    m2 = re.search(r'Manufacturer_Part_Number\s*</Defn>\s*<Defn[^>]*>([^<]+)', text)
    if m2:
        mpn = m2.group(1).strip()
    if not mpn:
        mpn = part
    return part, fp, mpn


def fix_il_paths(allegro_dir: Path, name: str, step_dir: Path) -> None:
    """Point the generated SKILL scripts at the permanent 3D folder."""
    old = step_dir.as_posix()
    new = MODELS.as_posix()
    for il in allegro_dir.glob("*.il"):
        try:
            text = il.read_text(encoding="utf-8", errors="replace")
            text = text.replace(old, new)
            text = text.replace(old.replace("/", "\\"), new.replace("/", "\\"))
            il.write_text(text, encoding="utf-8")
        except Exception as exc:
            print(f"[warn] cannot update {il}: {exc}")


def make_import_tcl(name: str) -> Path:
    tcl = SCRIPTS / f"import_{name}.tcl"
    xml = (SOURCES / "LCSC" / name / f"{name}.xml").as_posix()
    olb = (SYMBOLS / f"{name}.olb").as_posix()
    tcl.write_text(
        f'XMATIC_XML2OLB "{xml}" "{olb}" 1\n',
        encoding="utf-8",
    )
    return tcl


def make_build_notes(name: str, allegro_dir: Path, xml_path: Path) -> Path:
    notes = STAGING / name / f"README_{name}.txt"
    notes.write_text(
        f"""LCSC / npnp export: {name}
====================================

1) Capture symbol:
   XML: {xml_path}
   Recommended OLB: {(SYMBOLS / f'{name}.olb').as_posix()}

   GUI:
     File -> Import -> Library XML
     XML File = {xml_path}
     OLB File = {(SYMBOLS / f'{name}.olb').as_posix()}

   Or, in Capture's Command Window:
     source "{make_import_tcl(name).as_posix()}"

2) Allegro footprint:
   In the Allegro command window:
     skill load("{allegro_dir.as_posix()}/{name}.il")

   This generates .dra/.psm/.pad in:
     {allegro_dir}

3) Promote after the footprint is generated:
   python "{(HERE / 'cadence_lib.py').as_posix()}" promote {name} --clean

3D model folder:
   {MODELS.as_posix()}
""",
        encoding="utf-8",
    )
    return notes


def export_lcsc(cid: str, name: str, stage_only: bool = False) -> int:
    ensure_dirs()
    progress_step("导出 LCSC 数据（npnp）")
    stage = STAGING / name
    if stage.exists():
        print(f"[info] staging exists, reusing: {stage}")
    code, out = run_capture(
        ["cadence", cid, "--full", "-o", str(stage), "--name", name, "--language", "en"]
    )
    print(out.rstrip())
    if code != 0:
        progress_step("npnp 导出失败", "error")
        print(f"[error] npnp cadence exited with {code}", file=sys.stderr)
        return code
    progress_step("npnp 导出完成", "done")

    xml = stage / f"{name}.xml"
    allegro_dir = stage / f"{name}_allegro"
    step_dir = stage / f"{name}_step"
    if not xml.exists():
        progress_step("未找到导出的 XML", "error")
        print(f"[error] XML not found: {xml}", file=sys.stderr)
        return 1
    progress_step("检查导出文件", "done")

    step_files = sorted(step_dir.glob("*.step")) + sorted(step_dir.glob("*.stp"))
    if not step_files:
        print("[warn] no STEP file found", file=sys.stderr)

    # Keep the staging scripts runnable, but point them at the permanent 3D dir.
    if allegro_dir.exists():
        fix_il_paths(allegro_dir, name, step_dir)

    if stage_only:
        progress_step("已导出到 staging", "done")
        print(f"[stage-only] files are in {stage}")
        return 0

    # Copy 3D to the permanent folder.
    for step in step_files:
        shutil.copy2(step, MODELS / step.name)
        print(f"[3D] {MODELS / step.name}")
    if step_files:
        progress_step("复制 3D 模型", "done")

    # Archive the XML source.
    src_dir = SOURCES / "LCSC" / name
    src_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(xml, src_dir / xml.name)
    print(f"[source] {src_dir / xml.name}")
    if allegro_dir.exists():
        src_allegro = src_dir / "allegro"
        src_allegro.mkdir(parents=True, exist_ok=True)
        for il in allegro_dir.glob("*.il"):
            shutil.copy2(il, src_allegro / il.name)
        print(f"[source] {src_allegro}")

    progress_step("归档 XML / IL 源文件", "done")
    part, footprint, mpn = parse_xml_meta(xml)
    upsert_manifest(
        {
            "Source": "LCSC",
            "LCSC_ID": cid,
            "MPN": mpn or part,
            "Symbol": f"{name}.olb",
            "Footprint": footprint,
            "3D": step_files[0].name if step_files else "",
            "Date": date.today().isoformat(),
        }
    )
    progress_step("更新 manifest", "done")

    make_import_tcl(name)
    make_build_notes(name, allegro_dir, xml)
    progress_step("生成导入脚本", "done")
    print("\n下一步：")
    print(f"  1) Capture: File -> Import -> Library XML")
    print(f"       XML: {xml}")
    print(f"       OLB: {(SYMBOLS / f'{name}.olb')}")
    print(f"     或在 Capture Command Window: source \"{make_import_tcl(name)}\"")
    print(f"  2) Allegro Command Window:")
    print(f"       skill load(\"{allegro_dir.as_posix()}/{name}.il\")")
    print(f"  3) 生成封装后: python \"{(HERE / 'cadence_lib.py').as_posix()}\" promote {name} --clean")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    cid = args.component.strip()
    if not re.fullmatch(r"C\d+", cid, re.I):
        print(f"[error] LCSC ID should look like C165948, got: {cid}", file=sys.stderr)
        return 2
    name = args.name or cid
    return export_lcsc(cid, name, stage_only=args.stage_only)


def promote_stage(name: str, clean: bool = False) -> int:
    progress_step("复制封装到 Footprints")
    stage = STAGING / name
    allegro_dir = stage / f"{name}_allegro"
    if not allegro_dir.exists():
        progress_step("未找到 staging 封装目录", "error")
        print(f"[error] not found: {allegro_dir}", file=sys.stderr)
        return 1
    files = [
        p
        for p in allegro_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".dra", ".psm", ".pad")
    ]
    if not files:
        progress_step("没有可复制的封装文件", "error")
        print(f"[error] no .dra/.psm/.pad in {allegro_dir}", file=sys.stderr)
        return 1
    for f in files:
        shutil.copy2(f, FOOTPRINTS / f.name)
        print(f"[footprint] {FOOTPRINTS / f.name}")
    progress_step("封装复制完成", "done")

    for step in list((stage / f"{name}_step").glob("*.step")) + list(
        (stage / f"{name}_step").glob("*.stp")
    ):
        shutil.copy2(step, MODELS / step.name)
        print(f"[3D] {MODELS / step.name}")
    progress_step("复制 3D 模型", "done")

    if clean:
        shutil.rmtree(stage, ignore_errors=True)
        progress_step("清理 staging", "done")
        print(f"[clean] removed {stage}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    return promote_stage(args.name, clean=args.clean)


# ---------------------------------------------------------------------------
# sync Library Loader / staging
# ---------------------------------------------------------------------------
EXT_MAP = {
    ".olb": SYMBOLS,
    ".xml": SOURCES / "SamacSys",
    ".dra": FOOTPRINTS,
    ".psm": FOOTPRINTS,
    ".pad": FOOTPRINTS,
    ".stp": MODELS,
    ".step": MODELS,
}


def sync_downloads(days: int = 7, move: bool = False) -> int:
    ensure_dirs()
    progress_step("扫描 Downloads")
    if not DOWNLOADS.exists():
        progress_step("Downloads 目录不存在", "error")
        print(f"[warn] Downloads not found: {DOWNLOADS}")
        return 0
    cutoff = None
    if days > 0:
        import time

        cutoff = time.time() - days * 86400
    copied = 0
    for p in DOWNLOADS.iterdir():
        if not p.is_file():
            continue
        dest_dir = EXT_MAP.get(p.suffix.lower())
        if not dest_dir:
            continue
        if cutoff is not None and p.stat().st_mtime < cutoff:
            continue
        dest = dest_dir / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_mtime >= p.stat().st_mtime:
            continue
        if move:
            shutil.move(str(p), str(dest))
        else:
            shutil.copy2(p, dest)
        print(f"[sync] {p} -> {dest}")
        copied += 1
    print(f"[sync] {copied} file(s)")
    progress_step(f"同步 Downloads：{copied} 个文件", "done")
    return 0


def sync_staging() -> int:
    ensure_dirs()
    progress_step("扫描 staging")
    if not STAGING.exists():
        progress_step("staging 目录不存在", "done")
        return 0
    total = 0
    for stage in sorted(STAGING.iterdir()):
        if not stage.is_dir():
            continue
        name = stage.name
        allegro = stage / f"{name}_allegro"
        if allegro.exists():
            for f in allegro.iterdir():
                if f.is_file() and f.suffix.lower() in (".dra", ".psm", ".pad"):
                    shutil.copy2(f, FOOTPRINTS / f.name)
                    print(f"[staging] {f} -> {FOOTPRINTS / f.name}")
                    total += 1
        step_dir = stage / f"{name}_step"
        if step_dir.exists():
            for f in list(step_dir.glob("*.step")) + list(step_dir.glob("*.stp")):
                shutil.copy2(f, MODELS / f.name)
                print(f"[staging] {f} -> {MODELS / f.name}")
                total += 1
        src = stage / f"{name}.xml"
        if src.exists():
            dest_dir = SOURCES / "LCSC" / name
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / src.name)
            print(f"[staging] {src} -> {dest_dir / src.name}")
            total += 1
        if allegro.exists():
            dest_il = SOURCES / "LCSC" / name / "allegro"
            dest_il.mkdir(parents=True, exist_ok=True)
            for il in allegro.glob("*.il"):
                shutil.copy2(il, dest_il / il.name)
                print(f"[staging] {il} -> {dest_il / il.name}")
                total += 1
    print(f"[staging] {total} file(s)")
    progress_step(f"归档 staging：{total} 个文件", "done")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    if args.what in ("downloads", "all"):
        sync_downloads(days=args.days, move=args.move)
    if args.what in ("staging", "all"):
        sync_staging()
    return 0


# ---------------------------------------------------------------------------
# check / list
# ---------------------------------------------------------------------------
def ole_strings(data: bytes) -> list[str]:
    return [s.decode("latin1") for s in re.findall(rb"[ -~]{3,}", data)]


def cmd_check(args: argparse.Namespace) -> int:
    if olefile is None:
        progress_step("缺少 olefile 依赖", "error")
        print("[error] olefile is required: pip install olefile", file=sys.stderr)
        return 2
    ensure_dirs()
    progress_step("扫描符号库")
    dra = {p.stem.lower(): p for p in FOOTPRINTS.glob("*.dra")}
    psm = {p.stem.lower(): p for p in FOOTPRINTS.glob("*.psm")}
    models = {p.name.lower(): p for p in list(MODELS.glob("*.stp")) + list(MODELS.glob("*.step"))}

    rows = []
    for olb in sorted(SYMBOLS.glob("*.olb")):
        try:
            o = olefile.OleFileIO(str(olb))
        except Exception as exc:
            print(f"[warn] cannot open {olb}: {exc}")
            continue
        try:
            parts = [e[1] for e in o.listdir() if e[0] == "Packages" and len(e) == 2]
            for part in parts:
                try:
                    data = o.openstream(["Packages", part]).read()
                except Exception:
                    data = b""
                strings = ole_strings(data)
                fp_name = ""
                for s in strings:
                    if s.lower() in dra:
                        fp_name = dra[s.lower()].stem
                        break
                if not fp_name and part.lower() in dra:
                    fp_name = dra[part.lower()].stem

                dra_file = dra.get(fp_name.lower()) if fp_name else None
                psm_file = psm.get(fp_name.lower()) if fp_name else None

                linked_models = []
                if dra_file:
                    d = dra_file.read_bytes()
                    for m in re.findall(rb"[A-Za-z0-9_.\-]+\.(?:stp|step)", d, re.I):
                        linked_models.append(m.decode("latin1"))
                if psm_file:
                    d = psm_file.read_bytes()
                    for m in re.findall(rb"[A-Za-z0-9_.\-]+\.(?:stp|step)", d, re.I):
                        linked_models.append(m.decode("latin1"))
                linked_models = sorted(set(linked_models), key=str.lower)
                existing = [m for m in linked_models if m.lower() in models]
                rows.append(
                    {
                        "olb": olb.name,
                        "part": part,
                        "footprint": fp_name or "?",
                        "dra": "yes" if dra_file else "NO",
                        "psm": "yes" if psm_file else "NO",
                        "models": ", ".join(linked_models) if linked_models else "-",
                        "model_ok": "yes" if existing else "NO",
                    }
                )
        finally:
            o.close()

    progress_step("扫描符号库", "done")
    progress_step("匹配封装与 3D", "done")
    print(f"{'Symbol':30} {'Part':28} {'Footprint':42} {'DRA':4} {'PSM':4} {'3D':6}")
    print("-" * 125)
    for r in rows:
        print(
            f"{r['olb']:30} {r['part']:28} {r['footprint']:42} "
            f"{r['dra']:4} {r['psm']:4} {r['model_ok']:6}"
        )
    print()
    for r in rows:
        if r["model_ok"] != "yes":
            print(f"[3D] {r['olb']} / {r['part']}: linked={r['models']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    ensure_dirs()
    print(f"Root: {ROOT}")
    print(f"Symbols: {len(list(SYMBOLS.glob('*.olb')))} .olb")
    print(f"Footprints: {len(list(FOOTPRINTS.glob('*.dra')))} .dra, "
          f"{len(list(FOOTPRINTS.glob('*.psm')))} .psm, "
          f"{len(list(FOOTPRINTS.glob('*.pad')))} .pad")
    print(f"3D: {len(list(MODELS.glob('*.stp'))) + len(list(MODELS.glob('*.step')))} models")
    print()
    rows = read_manifest()
    if rows:
        print(f"{'Source':10} {'LCSC_ID':10} {'Symbol':28} {'Footprint':42} {'3D'}")
        print("-" * 115)
        for r in rows:
            print(
                f"{r.get('Source',''):10} {r.get('LCSC_ID',''):10} "
                f"{r.get('Symbol',''):28} {r.get('Footprint',''):42} {r.get('3D','')}"
            )
    return 0


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
def cmd_open_loader(args: argparse.Namespace) -> int:
    if LIBRARY_LOADER.exists():
        subprocess.Popen([str(LIBRARY_LOADER)])
        print(f"Started: {LIBRARY_LOADER}")
    else:
        print(f"[error] not found: {LIBRARY_LOADER}", file=sys.stderr)
        return 1
    return 0


def cmd_open_lib(args: argparse.Namespace) -> int:
    os.startfile(str(ROOT))  # type: ignore[attr-defined]
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def cmd_gui_ttk(args: argparse.Namespace) -> int:
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    try:
        import ttkbootstrap as ttk
        from ttkbootstrap import ScrolledText
    except ImportError:
        print("请先安装 ttkbootstrap：python -m pip install ttkbootstrap", file=sys.stderr)
        return 1

    root = ttk.Window(themename="darkly")
    root.title("CadenceLibHub")
    # ttkbootstrap applies a Tk scaling factor (often ~2.3 on HiDPI). Keep
    # the logical geometry small enough that the physical window fits the screen.
    root.geometry("1000x560")
    root.minsize(800, 500)

    style = ttk.Style()
    style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    header = ttk.Frame(root, padding=(20, 18, 20, 8))
    header.pack(fill=tk.X)
    ttk.Label(header, text="CadenceLibHub", font=("Segoe UI", 22, "bold")).pack(side=tk.LEFT)
    ttk.Label(
        header,
        text="推荐顺序：搜索 → 添加 → Capture 导入 → Allegro 生成 → 生成封装 → 检查匹配",
        font=("Segoe UI", 9),
        bootstyle="info",
    ).pack(side=tk.LEFT, padx=(14, 0), pady=(10, 0))
    progress = ttk.Progressbar(header, mode="indeterminate", length=120, bootstyle="info")
    progress.pack(side=tk.RIGHT, padx=(10, 0))

    search_card = ttk.Frame(root, padding=(20, 6, 20, 6))
    search_card.pack(fill=tk.X)
    ttk.Label(search_card, text="搜索", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
    query = ttk.Entry(search_card, font=("Segoe UI", 11))
    query.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
    source = ttk.Combobox(
        search_card,
        values=["LCSC", "SamacSys", "全部"],
        width=10,
        state="readonly",
    )
    source.set("LCSC")
    source.pack(side=tk.LEFT, padx=(0, 8))
    search_btn = ttk.Button(search_card, text="搜索", bootstyle="primary")
    search_btn.pack(side=tk.LEFT)

    actions = ttk.Frame(root, padding=(20, 4, 20, 4))
    actions.pack(fill=tk.X)

    table_card = ttk.Frame(root, padding=(20, 4, 20, 4))
    table_card.pack(fill=tk.BOTH, expand=False)
    cols = ("idx", "mpn", "lcsc", "mfr", "model")
    tree = ttk.Treeview(table_card, columns=cols, show="headings", height=4)
    for col, width, title in [
        ("idx", 40, "#"),
        ("mpn", 300, "MPN"),
        ("lcsc", 100, "LCSC"),
        ("mfr", 220, "Manufacturer"),
        ("model", 60, "3D"),
    ]:
        tree.heading(col, text=title)
        tree.column(col, width=width, anchor="w")
    vsb = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    info_card = ttk.Frame(root, padding=(20, 4, 20, 4))
    info_card.pack(fill=tk.BOTH, expand=False, before=table_card)

    notebook = ttk.Notebook(info_card)
    notebook.pack(fill=tk.BOTH, expand=True)
    guide_tab = ttk.Frame(notebook)
    steps_tab = ttk.Frame(notebook)
    log_tab = ttk.Frame(notebook)
    notebook.add(guide_tab, text="操作指引")
    notebook.add(steps_tab, text="运行状态")
    notebook.add(log_tab, text="日志")

    guide_text = (
        "LCSC 流程（推荐）\n"
        "1. 搜索框输入型号或 C 编号，来源选 LCSC，点“搜索”。\n"
        "2. 双击结果行，或选中后点“添加选中”；知道编号可直接点“按编号添加”。\n"
        "3. 打开 Capture：File → Import → Library XML\n"
        "   XML: E:\\Cadence\\MyLib\\_staging\\<库名>\\<库名>.xml\n"
        "   OLB: E:\\Cadence\\MyLib\\Symbols\\<库名>.olb\n"
        "   也可运行：Scripts\\import_<库名>.tcl\n"
        "4. 打开 Allegro，命令窗口执行：\n"
        "   skill load(\"<library-root>/_staging/<库名>/<库名>_allegro/<库名>.il\")\n"
        "5. 回到本工具，点“生成封装”，输入库名，自动复制到 Footprints。\n"
        "6. 点“检查匹配”，确认符号/封装/3D 全部 yes。\n"
        "\n"
        "SamacSys 流程\n"
        "1. 来源选 SamacSys，点“搜索”，登录并下载模型。\n"
        "2. 下载完成后点“同步下载”。\n"
        "3. 点“检查匹配”。\n"
        "\n"
        "按钮顺序\n"
        "搜索 → 添加选中/按编号添加 → Capture 导入 → Allegro 生成 → 生成封装 → 检查匹配\n"
    )
    guide_box = ScrolledText(
        guide_tab,
        height=4,
        autohide=True,
        font=("Consolas", 9),
    )
    guide_box.pack(fill=tk.BOTH, expand=True)
    guide_box.insert(tk.END, guide_text)
    guide_box.configure(state=tk.DISABLED)
    notebook.select(guide_tab)

    steps_tree = ttk.Treeview(
        steps_tab, columns=("step", "status"), show="headings", height=4
    )
    steps_tree.heading("step", text="步骤")
    steps_tree.heading("status", text="状态")
    steps_tree.column("step", width=620, anchor="w")
    steps_tree.column("status", width=120, anchor="w")
    steps_tree.pack(fill=tk.BOTH, expand=True)
    steps_tree.tag_configure("running", foreground="#4ea8ff")
    steps_tree.tag_configure("done", foreground="#2ecc71")
    steps_tree.tag_configure("error", foreground="#ff5c5c")

    log_box = ScrolledText(
        log_tab,
        height=4,
        autohide=True,
        font=("Consolas", 9),
    )
    log_box.pack(fill=tk.BOTH, expand=True)
    table_card.pack_configure(expand=True)

    def log(text: str) -> None:
        if not text:
            return
        log_box.insert(tk.END, text + "\n")
        log_box.see(tk.END)

    def gui_progress(text: str, state: str = "running") -> None:
        labels = {"running": "进行中", "done": "完成", "error": "失败"}

        def apply() -> None:
            iid = text
            values = (text, labels.get(state, state))
            if steps_tree.exists(iid):
                steps_tree.item(iid, values=values, tags=(state,))
            else:
                steps_tree.insert("", "end", iid=iid, values=values, tags=(state,))
            steps_tree.see(iid)

        root.after(0, apply)

    def run_bg(fn) -> None:
        steps_tree.delete(*steps_tree.get_children())
        notebook.select(steps_tab)
        set_progress_callback(gui_progress)
        progress.start(10)

        def worker() -> None:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    fn()
            except Exception as exc:
                buf.write(f"\n[error] {exc}\n")
            text = buf.getvalue().strip()

            def finish() -> None:
                set_progress_callback(None)
                progress.stop()
                log(text)

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def populate(results: list[dict]) -> None:
        tree.delete(*tree.get_children())
        for r in results:
            tree.insert(
                "",
                "end",
                values=(r["idx"], r["mpn"], r["lcsc"], r["mfr"], r["model"]),
            )

    def do_search() -> None:
        kw = query.get().strip()
        if not kw:
            return
        label = source.get()
        src = {"LCSC": "lcsc", "SamacSys": "samacsys", "全部": "all"}.get(label, "lcsc")
        steps_tree.delete(*steps_tree.get_children())
        notebook.select(steps_tab)
        set_progress_callback(gui_progress)
        progress.start(10)
        log(f"搜索 [{label}]: {kw}")
        if src in ("samacsys", "all"):
            search_samacsys(kw)
        if src in ("lcsc", "all"):

            def worker() -> None:
                code, out, results = lcsc_search(kw, 30)

                def finish() -> None:
                    set_progress_callback(None)
                    progress.stop()
                    populate(results)
                    if results:
                        log(f"{len(results)} 条结果，双击行即可添加。")
                    else:
                        log(out or "(没有结果)")
                    if code != 0:
                        log(f"[error] LCSC search exited with {code}")

                root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()
        else:
            set_progress_callback(None)
            progress.stop()

    def do_add_selected() -> None:
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("LCSC", "先在表格里选一行。", parent=root)
            return
        cid = tree.set(sel[0], "lcsc")
        mpn = tree.set(sel[0], "mpn")
        name = simpledialog.askstring(
            "Library name",
            f"{mpn}\n库名（留空用编号 {cid}）：",
            parent=root,
        )
        log(f"添加 LCSC {cid} ...")
        run_bg(lambda: export_lcsc(cid, name or cid, stage_only=False))

    def do_add_id() -> None:
        cid = simpledialog.askstring("LCSC ID", "输入立创编号，例如 C165948：", parent=root)
        if not cid:
            return
        name = simpledialog.askstring("Library name", "库名（留空用编号）：", parent=root) or cid
        log(f"添加 LCSC {cid.strip()} ...")
        run_bg(lambda: export_lcsc(cid.strip(), name.strip(), stage_only=False))

    def do_promote() -> None:
        name = simpledialog.askstring(
            "Promote",
            "输入库名（例如 C165948 或 TypeC16P）：",
            parent=root,
        )
        if not name:
            return
        log(f"归档 {name.strip()} 到 Footprints ...")
        run_bg(lambda: promote_stage(name.strip(), clean=True))

    def do_sync() -> None:
        log("同步 Downloads / staging ...")
        run_bg(lambda: cmd_sync(argparse.Namespace(what="all", days=7, move=False)))

    def do_check() -> None:
        log("检查符号 / 封装 / 3D 匹配 ...")
        run_bg(lambda: cmd_check(argparse.Namespace()))

    def do_open_loader() -> None:
        cmd_open_loader(argparse.Namespace())

    def do_open_lib() -> None:
        os.startfile(str(ROOT))

    def clear_log() -> None:
        log_box.delete("1.0", tk.END)

    search_btn.configure(command=do_search)
    query.bind("<Return>", lambda e: do_search())
    tree.bind("<Double-1>", lambda e: do_add_selected())

    buttons = [
        ("添加选中", do_add_selected, "success"),
        ("按编号添加", do_add_id, "primary"),
        ("生成封装", do_promote, "info"),
        ("检查匹配", do_check, "secondary"),
        ("同步下载", do_sync, "warning"),
        ("启动 Loader", do_open_loader, "secondary"),
        ("打开 MyLib", do_open_lib, "secondary"),
        ("清空日志", clear_log, "danger"),
    ]
    for i, (text, cmd, bootstyle) in enumerate(buttons):
        btn = ttk.Button(
            actions,
            text=text,
            command=cmd,
            bootstyle=bootstyle,
            width=12,
        )
        btn.grid(row=i // 4, column=i % 4, sticky="ew", padx=3, pady=2)
    for c in range(4):
        actions.columnconfigure(c, weight=1)

    log("CadenceLibHub ready.")
    log(f"Root: {ROOT}")
    log("LCSC 搜索/添加；SamacSys 搜索会打开网站并启动 Library Loader，下载后点“同步 Downloads”。")
    query.focus_set()
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
def cmd_gui(args: argparse.Namespace) -> int:
    """Canvas UI first, then customtkinter, then ttkbootstrap."""
    try:
        import gui_smooth
        return gui_smooth.run()
    except ImportError as smooth_exc:
        print(f"canvas UI 不可用：{smooth_exc}")
        try:
            import gui_ctk
            return gui_ctk.run()
        except ImportError as ctk_exc:
            print(f"customtkinter 不可用：{ctk_exc}")
            print("回退到 ttkbootstrap GUI。")
            return cmd_gui_ttk(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CadenceLibHub - LCSC + SamacSys unified helper")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="search LCSC and/or SamacSys")
    s.add_argument("keyword")
    s.add_argument("--source", choices=["lcsc", "samacsys", "all"], default="lcsc")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("add", help="export an LCSC part to Cadence")
    a.add_argument("component", help="LCSC ID, e.g. C165948")
    a.add_argument("--name", help="library base name (default: the LCSC ID)")
    a.add_argument("--stage-only", action="store_true", help="only export to _staging")
    a.set_defaults(func=cmd_add)

    pr = sub.add_parser("promote", help="copy generated .dra/.psm/.pad to Footprints")
    pr.add_argument("name")
    pr.add_argument("--clean", action="store_true", help="remove the staging folder after copying")
    pr.set_defaults(func=cmd_promote)

    sy = sub.add_parser("sync", help="sync Library Loader downloads and staging")
    sy.add_argument("--what", choices=["downloads", "staging", "all"], default="all")
    sy.add_argument("--days", type=int, default=7)
    sy.add_argument("--move", action="store_true")
    sy.set_defaults(func=cmd_sync)

    c = sub.add_parser("check", help="check symbol/footprint/3D matching")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("list", help="show library inventory")
    l.set_defaults(func=cmd_list)

    ol = sub.add_parser("open-loader", help="start SamacSys Library Loader")
    ol.set_defaults(func=cmd_open_loader)

    op = sub.add_parser("open-lib", help="open MyLib in Explorer")
    op.set_defaults(func=cmd_open_lib)

    g = sub.add_parser("gui", help="open a simple GUI")
    g.set_defaults(func=cmd_gui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
