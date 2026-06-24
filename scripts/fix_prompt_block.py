#!/usr/bin/env python3
"""Fix ALL broken prompt blocks in telegram.py."""
content = open('/home/user/baw/core/messaging/telegram.py', 'r').read()

# Fix 1: caption branch
for old, new in [
    (
        'f"[File: {file_name}]\n"\n                    f"[Type:',
        'f"[File: {file_name}]\\n"\n                    f"[Type:'
    ),
    (
        'f"report back what matters. Be that person."\n                )\n\n            self.send(chat_id, f"🤔 Analyzing',
        'f"report back what matters. Be that person."\n                )\n\n            self.send(chat_id, f"🤔 Analyzing'
    ),
]:
    if old != new:
        content = content.replace(old, new)

# Fix 2: Also fix caption branch - the whole block
old_caption = """                prompt = (
                    f"[File: {file_name}]
"
                    f"[Type: {doc.get('mime_type', 'unknown')}]\\n\\n"
                    f"The full text of this document has been extracted and saved to:\\n"
                    f"  {_extracted_path}\\n"
                    f"  Size: {_content_size} chars, ~{_content_pages} pages\\n\\n"
                    f"Use `read_file` with offset/limit to read it in chunks (500 lines at a time).\\n"
                    f"Use `search_files` with regex to find keywords across the document.\\n"
                    f"Do NOT try to read the entire file at once — read and analyze section by section.\\n\\n"
                    f"User instruction: {caption}"
                )"""

new_caption = """                prompt = (
                    f"[File: {file_name}]\\n"
                    f"[Type: {doc.get('mime_type', 'unknown')}]\\n\\n"
                    f"The full text of this document has been extracted and saved to:\\n"
                    f"  {_extracted_path}\\n"
                    f"  Size: {_content_size} chars, ~{_content_pages} pages\\n\\n"
                    f"Use `read_file` with offset/limit to read it in chunks (500 lines at a time).\\n"
                    f"Use `search_files` with regex to find keywords across the document.\\n"
                    f"Do NOT try to read the entire file at once — read and analyze section by section.\\n\\n"
                    f"User instruction: {caption}"
                )"""

if old_caption in content:
    content = content.replace(old_caption, new_caption)
    print("Fixed caption branch!")
else:
    print("Caption block not found with exact match, trying substring...")
    # Try just the broken line
    for i in range(100):
        broken = f'f"[File: {file_name}]\n"\n                    f"[Type:'
        fixed = f'f"[File: {file_name}]\\n"\n                    f"[Type:'
        if broken in content:
            content = content.replace(broken, fixed)
            print("Fixed broken [File: line!")
            break

open('/home/user/baw/core/messaging/telegram.py', 'w').write(content)

# Verify syntax
import py_compile
try:
    py_compile.compile('/home/user/baw/core/messaging/telegram.py', doraise=True)
    print("Syntax OK!")
except py_compile.PyCompileError as e:
    print(f"Still broken: {e}")
