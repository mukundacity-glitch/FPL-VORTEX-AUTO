from __future__ import annotations

import ast
import collections
import hashlib
import json
import re
from pathlib import Path

NOTEBOOK = Path("FPL_VORTEX_WEEKLY.ipynb")
EXPECTED_SHA256 = "3859bc2c147d12ff22b8d654750e390d14507f68120bc394542b148f6b3eea40"


def source(nb: dict, index: int) -> str:
    return "".join(nb["cells"][index].get("source", []))


def set_source(nb: dict, index: int, value: str) -> None:
    nb["cells"][index]["source"] = value.splitlines(keepends=True)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one replacement target, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    raw = NOTEBOOK.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(
            f"Notebook safety hash mismatch: expected {EXPECTED_SHA256}, got {digest}"
        )
    nb = json.loads(raw.decode("utf-8"))

    s = source(nb, 0)
    s = replace_once(
        s,
        '''from google.colab import drive
from pathlib import Path
import shutil
import time

DRIVE_MOUNT = Path("/content/drive")
MY_DRIVE = DRIVE_MOUNT / "MyDrive"
''',
        '''from pathlib import Path
import os
import shutil
import time

RUNNING_IN_GITHUB = bool(os.getenv("GITHUB_ACTIONS"))
RUNNING_IN_COLAB = False

if RUNNING_IN_GITHUB:
    DRIVE_MOUNT = Path(os.environ.get("FPL_VORTEX_LOCAL_ROOT", "output")).resolve()
    MY_DRIVE = DRIVE_MOUNT / "MyDrive"
    MY_DRIVE.mkdir(parents=True, exist_ok=True)
else:
    try:
        from google.colab import drive
        RUNNING_IN_COLAB = True
    except Exception as exc:
        raise RuntimeError(
            "FPL VORTEX requires either Google Colab or GitHub Actions execution."
        ) from exc
    DRIVE_MOUNT = Path("/content/drive")
    MY_DRIVE = DRIVE_MOUNT / "MyDrive"

FPL_VORTEX_DATA_ROOT = Path(
    os.environ.get("FPL_VORTEX_DATA_ROOT", str(MY_DRIVE / "FPL_VORTEX_DATA"))
)
ELEMENTS_ROOT = Path(
    os.environ.get("FPL_VORTEX_ELEMENTS_ROOT", str(MY_DRIVE / "elements"))
)
''',
        "cell 0 environment owner",
    )
    s = replace_once(
        s,
        '''    if not MY_DRIVE.is_dir():
        try:
            drive.mount(
                str(DRIVE_MOUNT),
                force_remount=False,
                timeout_ms=300_000,
            )
        except Exception as exc:
            if not MY_DRIVE.is_dir():
                msg = str(exc)
                if "credential propagation" in msg.lower():
                    raise RuntimeError(
                        "Google Colab could not pass credentials to Drive. "
                        "No FPL VORTEX output was deleted. Reconnect a hosted "
                        "Colab runtime, authorize Drive, then run all again."
                    ) from None
                raise RuntimeError(
                    "Google Drive mount failed. No FPL VORTEX output was deleted."
                ) from exc

    if not MY_DRIVE.is_dir():
        raise RuntimeError(
            "Google Drive is not available at /content/drive/MyDrive. "
            "No FPL VORTEX output was deleted."
        )
''',
        '''    if RUNNING_IN_COLAB and not MY_DRIVE.is_dir():
        try:
            drive.mount(
                str(DRIVE_MOUNT),
                force_remount=False,
                timeout_ms=300_000,
            )
        except Exception as exc:
            if not MY_DRIVE.is_dir():
                msg = str(exc)
                if "credential propagation" in msg.lower():
                    raise RuntimeError(
                        "Google Colab could not pass credentials to Drive. "
                        "No FPL VORTEX output was deleted. Reconnect a hosted "
                        "Colab runtime, authorize Drive, then run all again."
                    ) from None
                raise RuntimeError(
                    "Google Drive mount failed. No FPL VORTEX output was deleted."
                ) from exc

    if not MY_DRIVE.is_dir():
        raise RuntimeError(
            f"FPL VORTEX storage root is not available: {MY_DRIVE}. "
            "No FPL VORTEX output was deleted."
        )
''',
        "cell 0 mount guard",
    )
    s = replace_once(
        s,
        '    expected = Path("/content/drive/MyDrive/FPL_VORTEX/FIRST VIDEO")\n',
        '    expected = MY_DRIVE / "FPL_VORTEX" / "FIRST VIDEO"\n',
        "cell 0 output safety root",
    )
    s = replace_once(
        s,
        '    work_root = Path("/content/FPL_VORTEX_FIRST_DAY_WORK")\n',
        '''    if RUNNING_IN_GITHUB:
        work_root = Path(
            os.environ.get(
                "FPL_VORTEX_WORK_ROOT",
                str(Path(os.environ.get("RUNNER_TEMP", ".")) / "FPL_VORTEX_FIRST_DAY_WORK"),
            )
        ).resolve()
    else:
        work_root = Path("/content/FPL_VORTEX_FIRST_DAY_WORK")
''',
        "cell 0 work root",
    )
    s = s.replace(
        'print("✅ Google Drive verified writable before output cleanup")',
        'print("✅ Storage workspace verified writable before output cleanup")',
    )
    s = s.replace(
        'print("📂 Drive folders: MP4 • MP3 • SLIDE • DATA")',
        'print("📂 Output folders: MP4 • MP3 • SLIDE • DATA")',
    )
    s = s.replace(
        'print("🧪 Temporary HTML/timing/render files stay in /content only")',
        'print(f"🧪 Temporary HTML/timing/render files stay in {WORK_ROOT} only")',
    )
    set_source(nb, 0, s)

    exact = {
        5: [
            (
                'PHASE1_FPL_CORE_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data")',
                'PHASE1_FPL_CORE_ROOT = Path(FPL_VORTEX_DATA_ROOT) / "00_SOURCE_ARCHIVE" / "FPL_CORE" / "data"',
            )
        ],
        16: [
            (
                'PHASE8_FPL_CORE_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data")',
                'PHASE8_FPL_CORE_ROOT = Path(FPL_VORTEX_DATA_ROOT) / "00_SOURCE_ARCHIVE" / "FPL_CORE" / "data"',
            )
        ],
        18: [
            (
                'PHASE10_FPL_CORE_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data")',
                'PHASE10_FPL_CORE_ROOT = Path(FPL_VORTEX_DATA_ROOT) / "00_SOURCE_ARCHIVE" / "FPL_CORE" / "data"',
            )
        ],
        19: [
            (
                'PHASE11_INPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/MODEL_INPUTS")',
                'PHASE11_INPUT_ROOT = Path(PROJECT_ROOT) / "MODEL_INPUTS"',
            ),
            (
                'PHASE11_ELITE_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX_DATA/03_ELITE_MANAGERS")',
                'PHASE11_ELITE_ROOT = Path(FPL_VORTEX_DATA_ROOT) / "03_ELITE_MANAGERS"',
            ),
            (
                'PHASE11_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/DECISION_ENGINE")',
                'PHASE11_OUTPUT_ROOT = Path(PROJECT_ROOT) / "DECISION_ENGINE"',
            ),
        ],
        20: [
            (
                '        "/content/drive/MyDrive/FPL_VORTEX_DATA",',
                '        FPL_VORTEX_DATA_ROOT,',
            ),
            (
                'PHASE12_INPUT_ROOT = Path(globals().get("PHASE12_INPUT_ROOT_OVERRIDE", "/content/drive/MyDrive/FPL_VORTEX/MODEL_INPUTS"))',
                'PHASE12_INPUT_ROOT = Path(globals().get("PHASE12_INPUT_ROOT_OVERRIDE", Path(PROJECT_ROOT) / "MODEL_INPUTS"))',
            ),
        ],
        31: [
            (
                'ELEMENTS=Path(globals().get("MY_DRIVE", "/content/drive/MyDrive")) / "elements"',
                'ELEMENTS=Path(globals().get("ELEMENTS_ROOT", Path(MY_DRIVE) / "elements"))',
            )
        ],
    }
    for index, replacements in exact.items():
        s = source(nb, index)
        for old, new in replacements:
            s = replace_once(s, old, new, f"cell {index}")
        set_source(nb, index, s)

    s = source(nb, 28)
    s = replace_once(
        s,
        "from google.colab import output\n",
        '''if bool(globals().get("RUNNING_IN_COLAB", False)):
    from google.colab import output as _vx_colab_output
else:
    _vx_colab_output = None
''',
        "cell 28 preview import",
    )
    s = replace_once(
        s,
        '''output.serve_kernel_port_as_iframe(
    _VX_ALL_SLIDES_PREVIEW_PORT,
    path=f"/all_selected_slides_preview.html?v={_cache_buster}",
    width="100%",
    height=980,
    cache_in_notebook=True,
)''',
        '''if _vx_colab_output is not None:
    _vx_colab_output.serve_kernel_port_as_iframe(
        _VX_ALL_SLIDES_PREVIEW_PORT,
        path=f"/all_selected_slides_preview.html?v={_cache_buster}",
        width="100%",
        height=980,
        cache_in_notebook=True,
    )
else:
    print(f"✅ Preview HTML ready: {ALL_SLIDES_PREVIEW_HTML}")
''',
        "cell 28 preview display",
    )
    set_source(nb, 28, s)

    fallback_replacements = {
        'Path(globals().get("PROJECT_ROOT", "/content/drive/MyDrive/FPL_VORTEX"))': 'Path(globals().get("PROJECT_ROOT", Path("output/MyDrive/FPL_VORTEX")))',
        'Path(globals().get("MODULE_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/vortex"))': 'Path(globals().get("MODULE_DIR", Path("output/.work/vortex")))',
        'Path(globals().get("DATA_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/data"))': 'Path(globals().get("DATA_DIR", Path("output/.work/data")))',
        'Path(globals().get("HTML_WORK_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/html"))': 'Path(globals().get("HTML_WORK_DIR", Path("output/.work/html")))',
        'Path(globals().get("AUDIO_WORK_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/audio"))': 'Path(globals().get("AUDIO_WORK_DIR", Path("output/.work/audio")))',
        'Path(globals().get("VIDEO_WORK_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/video"))': 'Path(globals().get("VIDEO_WORK_DIR", Path("output/.work/video")))',
        'Path(globals().get("PREVIEW_WORK_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/preview"))': 'Path(globals().get("PREVIEW_WORK_DIR", Path("output/.work/preview")))',
        'Path(globals().get("MP4_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/mp4"))': 'Path(globals().get("MP4_DIR", Path("output/.work/mp4")))',
        'Path(globals().get("MP3_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/mp3"))': 'Path(globals().get("MP3_DIR", Path("output/.work/mp3")))',
        'Path(globals().get("SLIDE_DIR", "/content/FPL_VORTEX_FIRST_DAY_WORK/slide"))': 'Path(globals().get("SLIDE_DIR", Path("output/.work/slide")))',
        'Path(globals().get("WORK_ROOT", "/content/FPL_VORTEX_FIRST_DAY_WORK"))': 'Path(globals().get("WORK_ROOT", Path("output/.work")))',
    }
    for index, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        s = source(nb, index)
        for old, new in fallback_replacements.items():
            s = s.replace(old, new)
        set_source(nb, index, s)

    s = source(nb, 29)
    s = replace_once(
        s,
        '''    DESIGN_W, DESIGN_H = 3840, 2160
    WW = int(globals().get("VIDEO_WIDTH", 3840))
H = int(globals().get("VIDEO_HEIGHT", 2160))
FPS = int(globals().get("VIDEO_FPS", 30))
    SCALE_X = W / DESIGN_W
''',
        '''    DESIGN_W, DESIGN_H = 3840, 2160
    W = int(globals().get("VIDEO_WIDTH", 3840))
    H = int(globals().get("VIDEO_HEIGHT", 2160))
    FPS = int(globals().get("VIDEO_FPS", 30))
    SCALE_X = W / DESIGN_W
''',
        "cell 29 pre-existing W/indentation bug",
    )
    set_source(nb, 29, s)

    duplicates: collections.defaultdict[str, list[int]] = collections.defaultdict(list)
    for index, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        s = source(nb, index)
        if (
            "/content/drive/MyDrive/FPL_VORTEX" in s
            or "/content/drive/MyDrive/FPL_VORTEX_DATA" in s
        ):
            raise RuntimeError(f"Cell {index} still contains a hard-coded Drive project path")
        if "drive.mount(" in s and index != 0:
            raise RuntimeError(f"Cell {index} contains an unexpected Drive mount")
        compile(s, f"<cell {index}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        for match in re.finditer(r"^def\s+([A-Za-z_]\w*)\s*\(", s, flags=re.M):
            duplicates[match.group(1)].append(index)

    duplicate_defs = {name: cells for name, cells in duplicates.items() if len(cells) > 1}
    if duplicate_defs:
        raise RuntimeError(f"Duplicate function definitions detected: {duplicate_defs}")

    NOTEBOOK.write_text(
        json.dumps(nb, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print("Notebook migration PASS")


if __name__ == "__main__":
    main()
