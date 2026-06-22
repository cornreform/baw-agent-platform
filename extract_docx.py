import zipfile
import xml.etree.ElementTree as ET

# Extract DOCX (it's a ZIP file)
docx_path = '/home/baw/.baw/downloads/CRCBM_011_contract_cn.docx'
with zipfile.ZipFile(docx_path, 'r') as z:
    with z.open('word/document.xml') as f:
        tree = ET.parse(f)
        root = tree.getroot()
        
        # Extract text from all paragraphs
        texts = []
        for para in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
            para_text = ''
            for run in para.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                if run.text:
                    para_text += run.text
            if para_text.strip():
                texts.append(para_text)
        
        full_text = '\n'.join(texts)
        with open('/tmp/crcbm_011_contract.txt', 'w', encoding='utf-8') as out:
            out.write(full_text)
        print(f"DOCX extracted: {len(full_text)} chars")
