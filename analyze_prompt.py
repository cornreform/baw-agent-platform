#!/usr/bin/env python3
"""Analyze the BAW _run_baw system prompt to find the rambling source."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/BAW"))
from core.loop import build_system_prompt
import yaml

with open(os.path.expanduser("~/.baw/config.yaml")) as f:
    config = yaml.safe_load(f)

prompt = build_system_prompt(config)

# Show first 3000 chars to see what BAW sees first
print("=== FIRST 3000 CHARS ===")
print(prompt[:3000])
print(f"\n\n=== TOTAL: {len(prompt)} chars ===")
