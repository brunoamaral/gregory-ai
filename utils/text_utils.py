
import re
import stopwords
from bs4 import BeautifulSoup

# Define some cleaning procedures:
REPLACE_BY_SPACE_RE = re.compile(r'[/(){}\[\]\|@,;]')
BAD_SYMBOLS_RE = re.compile(r'[^0-9a-z #+_]')
STOPWORDS = set(stopwords.get_stopwords("en"))

# Util function to clean text
def cleanText(text):
    """
        Cleans the input text by applying the following:
        
            * change to lowercase text
            * replace common symbols with a space
            * replace invalid symbols with empty char
            * remove stopwords from text
        
        Arguments
        ---------
        text: a string
        
        Output
        ------
        return: modified initial string
    """

    # change to lowercase text
    text = text.lower()

    # replace REPLACE_BY_SPACE_RE symbols by space in text. substitute the matched string in REPLACE_BY_SPACE_RE with space.
    text = REPLACE_BY_SPACE_RE.sub(' ', text)

    # remove symbols which are in BAD_SYMBOLS_RE from text. substitute the matched string in BAD_SYMBOLS_RE with nothing. 
    text = BAD_SYMBOLS_RE.sub('', text)

    # remove stopwords from text
    text = ' '.join(word for word in text.split() if word not in STOPWORDS)

    return text


def cleanHTML(input):
    return BeautifulSoup(input, 'html.parser').get_text()