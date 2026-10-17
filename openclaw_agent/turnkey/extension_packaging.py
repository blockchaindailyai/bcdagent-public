from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class ExtensionPackagingResult:
    schema: str
    extension_manifest: str
    installer_manifest: str
    skill_manifests: List[str]
    output_dir: str
    wave_1_complete: bool
    wave_2_complete: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def package_openclaw_extension(*, output_dir: str = "openclaw_agent/dist/openclaw-extension") -> ExtensionPackagingResult:
    root = _repo_root()
    extension_dir = root / "openclaw_agent" / "openclaw_extension"
    skills_dir = root / "openclaw_agent" / "skills"

    extension_manifest = extension_dir / "extension.manifest.json"
    installer_manifest = extension_dir / "install" / "openclaw.extension.json"
    skill_manifests = sorted(str(path.relative_to(root)) for path in skills_dir.glob("*/skill.manifest.json"))

    out_dir = (root / output_dir).resolve()
    allowed_root = (root / "openclaw_agent" / "dist").resolve()
    if out_dir == allowed_root or not out_dir.is_relative_to(allowed_root):
        raise ValueError("Extension output must be a child of openclaw_agent/dist")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(extension_manifest, out_dir / extension_manifest.name)
    (out_dir / "install").mkdir(parents=True, exist_ok=True)
    shutil.copy2(installer_manifest, out_dir / "install" / installer_manifest.name)

    packaged_skill_manifests: List[str] = []
    out_skills_dir = out_dir / "skills"
    out_skills_dir.mkdir(parents=True, exist_ok=True)
    for manifest_rel in skill_manifests:
        manifest_path = root / manifest_rel
        skill_dir = manifest_path.parent
        target_dir = out_skills_dir / skill_dir.name
        shutil.copytree(skill_dir, target_dir, dirs_exist_ok=True)
        packaged_skill_manifests.append(str((target_dir / manifest_path.name).relative_to(root)))

    with extension_manifest.open("r", encoding="utf-8") as fh:
        ext_payload: Dict[str, object] = json.load(fh)
    waves = ext_payload.get("wave_status", {}) if isinstance(ext_payload, dict) else {}

    return ExtensionPackagingResult(
        schema="openclaw-extension-packaging-v1",
        extension_manifest=str(extension_manifest.relative_to(root)),
        installer_manifest=str(installer_manifest.relative_to(root)),
        skill_manifests=packaged_skill_manifests,
        output_dir=str(out_dir.relative_to(root)),
        wave_1_complete=waves.get("wave_1") == "complete",
        wave_2_complete=waves.get("wave_2") == "complete",
    )


def packaging_result_to_dict(result: ExtensionPackagingResult) -> Dict[str, object]:
    return asdict(result)
