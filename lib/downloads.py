"""Safe HTTP download names, including non-Latin client names."""
import re
import unicodedata
from urllib.parse import quote


def content_disposition(filename):
    cleaned=re.sub(r'[\x00-\x1f\x7f/\\]', '_', str(filename))[:200]
    ascii_name=unicodedata.normalize('NFKD',cleaned).encode('ascii','ignore').decode()
    ascii_name=re.sub(r'[^A-Za-z0-9._ -]','_',ascii_name).strip(' .') or 'invoice.pdf'
    return 'attachment; filename="'+ascii_name+'"; filename*=UTF-8\'\''+quote(cleaned,safe='')
