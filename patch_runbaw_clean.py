#!/usr/bin/env python3
"""Add clean synthesis to _run_baw in messaging/__init__.py"""
import re
path = "/home/radxa/BAW/core/messaging/__init__.py"

with open(path, "r") as f:
    content = f.read()

# Find "return output.strip()" that belongs to _run_baw (not route() or others)
# The _run_baw function ends with this return, followed by "except BaseException"
pattern = "            return output.strip()\n\n        except BaseException as e:\n            return f\"[FAIL] BAW error: {e}\"\n\n    # ── Focus Mode"

replacement = """            # Clean synthesis: strip tool artifacts
            try:
                from ..loop import build_system_prompt
                from ..llm import call_llm_with_fallback
                _cs = build_system_prompt(config, os.path.expanduser("~/.baw"))
                _cm = [{"role": "system", "content": _cs}, {"role": "user", "content": prompt or ""}]
                _cf = call_llm_with_fallback(config, _cm, tools=None, temperature=0.7)
                if _cf.response and _cf.response.content and len(_cf.response.content.strip()) > 20:
                    output = _cf.response.content.strip()
            except Exception:
                pass
            return output.strip()

        except BaseException as e:
            return f"[FAIL] BAW error: {e}"

    # ── Focus Mode"""

if pattern in content:
    content = content.replace(pattern, replacement, 1)
    with open(path, "w") as f:
        f.write(content)
    print("CLEAN SYNTHESIS ADDED TO _run_baw")
else:
    print("PATTERN NOT FOUND")
    # Debug
    idx = content.find("return output.strip()")
    if idx >= 0:
        print(f"Found at {idx}: {repr(content[idx:idx+200])}")
