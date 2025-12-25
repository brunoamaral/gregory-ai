
import re
import stopwords
from bs4 import BeautifulSoup

# Define some cleaning procedures:
REPLACE_BY_SPACE_RE = re.compile(r'[/(){}\[\]\|@,;]')
BAD_SYMBOLS_RE = re.compile(r'[^0-9a-z #+_]')
WHITESPACE_RE = re.compile(r'[\n\t\r]+')  # Newlines, tabs, carriage returns
MULTIPLE_SPACES_RE = re.compile(r'\s+')  # Multiple whitespace characters
STOPWORDS = set(stopwords.get_stopwords("en"))

# DOI and PMID patterns (matching notebook preprocessing in files_repo_PBL_nsbe)
DOI_PATTERN_RE = re.compile(r'doi:?\s?\d{2}\.\d{4}/\S+', re.IGNORECASE)
DOI_PATTERN_SHORT_RE = re.compile(r'\d{2}\.\d{4}/\S+')
PMID_PATTERN_RE = re.compile(r'pmid:?\s?\d{8}', re.IGNORECASE)
PMID_SHORT_RE = re.compile(r'\b\d{8}\b')

# Minimum word count threshold (matching notebook preprocessing)
MIN_WORD_COUNT = 10


def cleanText(text, min_words=None):
    """
        Cleans the input text by applying the following:
        
            * normalize whitespace (newlines, tabs, carriage returns to spaces)
            * change to lowercase text
            * remove DOI patterns (e.g., doi:10.1234/xyz or 10.1234/xyz)
            * remove PMID patterns (e.g., pmid:12345678 or 12345678)
            * replace common symbols with a space
            * replace invalid symbols with empty char
            * remove stopwords from text
            * collapse multiple spaces into single space
            * optionally filter out texts with fewer than min_words words
        
        Arguments
        ---------
        text: a string
        min_words: int or None. If provided, returns None for texts with fewer 
                   than this many words. Use MIN_WORD_COUNT constant (10) to match
                   notebook preprocessing. Default is None (no filtering).
        
        Output
        ------
        return: modified initial string, or None if text is empty/too short
    """
    if not text or not isinstance(text, str):
        return None

    # normalize whitespace: convert newlines, tabs, carriage returns to spaces
    text = WHITESPACE_RE.sub(' ', text)

    # change to lowercase text
    text = text.lower()

    # remove DOI patterns (matching notebook preprocessing)
    text = DOI_PATTERN_RE.sub('', text)
    text = DOI_PATTERN_SHORT_RE.sub('', text)

    # remove PMID patterns (matching notebook preprocessing)
    text = PMID_PATTERN_RE.sub('', text)
    text = PMID_SHORT_RE.sub('', text)

    # replace REPLACE_BY_SPACE_RE symbols by space in text
    text = REPLACE_BY_SPACE_RE.sub(' ', text)

    # remove symbols which are in BAD_SYMBOLS_RE from text
    text = BAD_SYMBOLS_RE.sub('', text)

    # collapse multiple spaces into single space
    text = MULTIPLE_SPACES_RE.sub(' ', text)

    # strip leading/trailing whitespace
    text = text.strip()

    # remove stopwords from text
    words = [word for word in text.split() if word not in STOPWORDS]
    text = ' '.join(words)

    # check for empty or whitespace-only result
    if not text or text.isspace():
        return None

    # optionally filter by minimum word count (matching notebook preprocessing)
    if min_words is not None and len(words) < min_words:
        return None

    return text


def cleanHTML(input):
    """
        Remove HTML tags and return plain text.
        
        Arguments
        ---------
        input: a string containing HTML
        
        Output
        ------
        return: plain text with HTML tags removed
    """
    if not input:
        return ""
    return BeautifulSoup(input, 'html.parser').get_text()