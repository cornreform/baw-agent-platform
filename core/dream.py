"""
BAW — Dreaming: Weekly self-curation of SOUL.md and memory
Reads recent interactions, patches SOUL.md if needed, compacts memory.
Silent unless there's a problem.
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone


def dream(data_dir: Path, dry_run: bool = False) -> dict:
    """
    Weekly self-curation pass.
    Returns a report dict (empty if nothing changed = silent).
    """
    soul_path = data_dir / "SOUL.md"
    memory_path = data_dir / "memory" / "store.jsonl"
    report = {"soul_patched": False, "memory_archived": 0, "changes": []}

    # --- Step 1: Audit SOUL.md ---
    if soul_path.exists():
        soul = soul_path.read_text(encoding="utf-8")
        before_len = len(soul)
        
        # Check for stale timestamp references (older than 30 days)
        dates = re.findall(r'\d{4}-\d{2}-\d{2}', soul)
        stale_dates = []
        for d in dates:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                if (datetime.now() - dt).days > 30:
                    stale_dates.append(d)
            except ValueError:
                pass
        
        if stale_dates:
            report["changes"].append(f"Found stale dates in SOUL.md: {stale_dates}")
            # Don't auto-remove dates, just report them
        
        # Check SOUL.md size
        if before_len > 4000:
            report["changes"].append(f"SOUL.md is {before_len} chars — consider trimming")
    
    # --- Step 2: Compact memory ---
    if memory_path.exists():
        lines = memory_path.read_text(encoding="utf-8").strip().split("\n")
        total = len([l for l in lines if l.strip()])
        
        # Find low-score entries (score < 0.15) for archival
        low_score = []
        kept = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                score = entry.get("score", 0)
                if score < 0.15:
                    low_score.append(entry)
                else:
                    kept.append(line)
            except json.JSONDecodeError:
                kept.append(line)
        
        if low_score:
            archive_path = data_dir / "memory" / f"archive-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
            if not dry_run:
                with open(archive_path, "w", encoding="utf-8") as f:
                    for entry in low_score:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                # Rewrite store without low-score entries
                with open(memory_path, "w", encoding="utf-8") as f:
                    for line in kept:
                        f.write(line + "\n")
            
            report["memory_archived"] = len(low_score)
            report["changes"].append(f"Archived {len(low_score)} low-score entries to {archive_path.name}")
    
    # --- Step 3: Run memory decay ---
    if memory_path.exists():
        try:
            from baw.core.memory import MemoryStore
            store = MemoryStore(data_dir)
        except (ImportError, ModuleNotFoundError):
            from .memory import MemoryStore
            store = MemoryStore(data_dir)
        if not dry_run:
            store.decay()
        report["changes"].append("Memory decay applied")
    
    # --- Step 4: Update dreaming timestamp ---
    if not dry_run and report["changes"]:
        # Add or update dreaming timestamp in SOUL.md
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dream_line = f"\n\n<!-- last-dream: {today} -->"
        
        if soul_path.exists():
            soul = soul_path.read_text(encoding="utf-8")
            # Replace existing dream timestamp
            if "<!-- last-dream:" in soul:
                soul = re.sub(r'<!-- last-dream:.*?-->', f'<!-- last-dream: {today} -->', soul)
            else:
                soul += dream_line
            soul_path.write_text(soul, encoding="utf-8")
    
    # --- Step 5: Self-evolution — behavior analysis + auto-optimize ---
    if not dry_run:
        try:
            from baw.core.evolve import auto_optimize
            evolve_result = auto_optimize(dry_run=False)
            if evolve_result.get("patterns_found", 0) > 0:
                report["changes"].append(
                    f"Evolution: {evolve_result['patterns_found']} patterns detected"
                )
                if evolve_result.get("soul_patched"):
                    report["changes"].append("Evolution: SOUL.md updated with learned preferences")
                if evolve_result.get("config_patched"):
                    report["changes"].append("Evolution: config updated with known issues")
        except ImportError:
            # Not installed as 'baw' package — try relative import
            try:
                from .evolve import auto_optimize
                evolve_result = auto_optimize(dry_run=False)
                if evolve_result.get("patterns_found", 0) > 0:
                    report["changes"].append(
                        f"Evolution: {evolve_result['patterns_found']} patterns detected"
                    )
                    if evolve_result.get("soul_patched"):
                        report["changes"].append("Evolution: SOUL.md updated with learned preferences")
                    if evolve_result.get("config_patched"):
                        report["changes"].append("Evolution: config updated with known issues")
            except Exception as e:
                report["changes"].append(f"Evolution: skipped ({e})")
    
    report["soul_patched"] = len(report["changes"]) > 0
    return report
