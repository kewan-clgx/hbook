import os
from dotenv import load_dotenv
load_dotenv()
from llama_parse import LlamaParse

parser = LlamaParse(
    api_key=os.environ['LLAMA_CLOUD_API_KEY'],
    result_type='markdown',
    premium_mode=True,
    parsing_instruction='Preserve all section numbers as markdown headings.',
    language='en',
)
docs = parser.load_data('hoa-docs/raw/wookdbury-hoa-001/94b2e271-e3ca-4423-bad1-cdd2eb0a7afc.pdf')
print(f'Documents returned: {len(docs)}')
if docs:
    print(f'First doc text length: {len(docs[0].text)}')
    print(f'First 200 chars: {docs[0].text[:200]}')
else:
    print('NO DOCUMENTS RETURNED')
