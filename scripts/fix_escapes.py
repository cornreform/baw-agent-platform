#!/usr/bin/env python3
"""Fix ALL broken f-string escape sequences in telegram.py."""
with open('/home/user/baw/core/messaging/telegram.py', 'r') as f:
    content = f.read()

lines = content.split('\n')
fixes = 0
i = 0
while i < len(lines):
    stripped = lines[i].strip()
    # Found an orphaned closing quote: line is just "
    if stripped == '"':
        # Scan backwards past blank lines to find the f-string it belongs to
        j = i - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        if j >= 0:
            prev_stripped = lines[j].strip()
            if prev_stripped.startswith('f"') and not prev_stripped.endswith('"'):
                # This orphaned " closes the f-string at line j
                # Add \n" to the end of the f-string line
                lines[j] = lines[j].rstrip() + '\\n"'
                # Remove the orphaned " line
                del lines[i]
                fixes += 1
                print(f"Fix {fixes}: line {j+1} ← orphaned quote at {i+1}")
                continue  # Don't increment i since we deleted current line
    i += 1

content = '\n'.join(lines)
with open('/home/user/baw/core/messaging/telegram.py', 'w') as f:
    f.write(content)

import py_compile
try:
    py_compile.compile('/home/user/baw/core/messaging/telegram.py', doraise=True)
    print(f'\nSyntax OK! ({fixes} fixes)')
except py_compile.PyCompileError as e:
    print(f'\nStill broken: {e}')
